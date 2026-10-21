"""Perplexity üzerinden piyasa ortalaması: önbellekli, hata türlerini ayıran istemci."""
from __future__ import annotations

import asyncio
import json
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.domain import PriceCache
from src.parsing import alnum_key, parse_tl

API_URL = "https://api.perplexity.ai/chat/completions"
MIN_PLAUSIBLE_TL = 50_000
MAX_PLAUSIBLE_TL = 50_000_000
KM_BUCKET = 25_000
BREAKER_LIMIT = 3  # döngü içinde üst üste bu kadar altyapı hatasında çağrıları durdur


class PriceServiceError(Exception):
    """Fiyat servisine ulaşılamadı / yetkisiz / limit. İlan değerlendirilemedi demektir."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind  # no_key | auth | quota | rate | http | network | breaker
        self.detail = detail


@dataclass(frozen=True)
class PriceEstimate:
    average: float
    low: float | None
    high: float | None
    source: str  # cache | perplexity


def _to_tl(raw: str) -> float:
    raw = raw.strip()
    if re.fullmatch(r"\d{1,3}(,\d{3})+", raw):  # 850,000 (İngilizce binlik ayracı)
        raw = raw.replace(",", "")
    return parse_tl(raw)


def _extract_numbers(text: str) -> list[float]:
    vals = []
    for m in re.finditer(r"\d[\d.,]*\d|\d", text):
        try:
            v = _to_tl(m.group())
        except ValueError:
            continue
        if MIN_PLAUSIBLE_TL <= v <= MAX_PLAUSIBLE_TL:
            vals.append(v)
    return vals


def parse_estimate(content: str) -> tuple[float, float | None, float | None] | None:
    """Modelin cevabından (ortalama, min, max) çıkarır. Anlamlı değer yoksa None."""
    if not content:
        return None
    text = re.sub(r"\[\d+\]", "", content)  # [1] [2] atıf işaretleri
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group())
            def num(*keys):
                for k in keys:
                    if k in obj and obj[k] not in (None, ""):
                        try:
                            v = _to_tl(str(obj[k])) if not isinstance(obj[k], (int, float)) else float(obj[k])
                        except ValueError:
                            continue
                        if MIN_PLAUSIBLE_TL <= v <= MAX_PLAUSIBLE_TL:
                            return v
                return None
            avg, low, high = num("ortalama", "average", "avg"), num("min", "en_dusuk"), num("max", "en_yuksek")
            if avg is None and low and high:
                avg = (low + high) / 2
            if avg is not None:
                return avg, low, high
        except (ValueError, TypeError):
            pass
    nums = _extract_numbers(text)
    if not nums:
        return None
    return float(statistics.median(nums)), min(nums), max(nums)


def cache_key(brand: str, model: str, trim: str, year: int, km: int) -> str:
    return f"{alnum_key(brand)}|{alnum_key(model)}|{alnum_key(trim)}|{year}|{(km or 0) // KM_BUCKET}"


class PriceService:
    def __init__(self, session: AsyncSession, api_key: str, model: str = "sonar",
                 cache_days: int = 7, client: httpx.AsyncClient | None = None,
                 retry_sleep: float = 2.0):
        self.session = session
        self.api_key = api_key
        self.model = model
        self.cache_ttl = timedelta(days=cache_days)
        self._client = client
        self._retry_sleep = retry_sleep
        self._memo: dict[str, PriceEstimate | None] = {}
        self.consecutive_failures = 0
        # döngü istatistikleri
        self.calls = 0
        self.failures = 0
        self.cache_hits = 0
        self.last_error_kind: str | None = None

    def reset_cycle(self) -> None:
        self.consecutive_failures = 0
        self.calls = self.failures = self.cache_hits = 0
        self.last_error_kind = None
        self._memo.clear()

    async def estimate(self, brand: str, model: str, trim: str, year: int, km: int) -> PriceEstimate | None:
        """Piyasa ortalaması. Fiyat bulunamazsa None; servis hatasında PriceServiceError."""
        key = cache_key(brand, model, trim, year, km)
        if key in self._memo:
            return self._memo[key]

        cached = (await self.session.execute(select(PriceCache).where(PriceCache.cache_key == key))).scalars().first()
        if cached and cached.fetched_at >= datetime.utcnow() - self.cache_ttl:
            self.cache_hits += 1
            est = PriceEstimate(cached.average, cached.low, cached.high, "cache")
            self._memo[key] = est
            return est

        if not self.api_key:
            raise PriceServiceError("no_key", "PERPLEXITY_API_KEY tanımlı değil")
        if self.consecutive_failures >= BREAKER_LIMIT:
            raise PriceServiceError("breaker", f"son {BREAKER_LIMIT} çağrı başarısız, bu döngüde durduruldu")

        self.calls += 1
        try:
            content = await self._ask(brand, model, trim, year, km)
        except PriceServiceError as exc:
            self.failures += 1
            self.consecutive_failures += 1
            self.last_error_kind = exc.kind
            raise
        self.consecutive_failures = 0

        parsed = parse_estimate(content)
        if parsed is None:
            logger.warning(f"Perplexity cevabı ayrıştırılamadı ({brand} {model} {year}): {content[:120]!r}")
            self._memo[key] = None
            return None
        avg, low, high = parsed

        if cached:
            cached.average, cached.low, cached.high, cached.fetched_at = avg, low, high, datetime.utcnow()
        else:
            self.session.add(PriceCache(cache_key=key, average=avg, low=low, high=high))
        await self.session.commit()
        est = PriceEstimate(avg, low, high, "perplexity")
        self._memo[key] = est
        return est

    async def _ask(self, brand: str, model: str, trim: str, year: int, km: int) -> str:
        brand = re.sub(r"\s*-\s*", "-", brand)  # "Mercedes - Benz" -> "Mercedes-Benz"
        if trim and alnum_key(trim).startswith(alnum_key(model)):  # "A" + "A 180 Urban" -> "A A 180" olmasın
            model = ""
        vehicle = " ".join(p for p in (str(year), "model", brand, model, trim) if p)
        km_txt = f"{km:,}".replace(",", ".") if km else "bilinmiyor"
        user = (
            f"Türkiye ikinci el otomobil piyasasında şu an satışta olan {vehicle}, yaklaşık {km_txt} km, "
            "ağır hasarsız ve normal bakımlı bir aracın güncel ilan fiyatı ortalaması kaç TL? "
            "Aynı seri, motor/donanım ve benzer kilometredeki güncel ilanları baz al "
            "(arabam.com, sahibinden.com, otomerkezi vb.). "
            'Yalnızca şu JSON\'u döndür: {"ortalama": <TL, tam sayı>, "min": <TL>, "max": <TL>}'
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Türkiye ikinci el araç piyasası fiyat analistisin. Yalnızca JSON döndür, açıklama yazma."},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=45.0)
        try:
            last: PriceServiceError | None = None
            for attempt in range(3):
                try:
                    resp = await client.post(API_URL, headers=headers, json=payload)
                except httpx.HTTPError as exc:
                    last = PriceServiceError("network", type(exc).__name__)
                else:
                    if resp.status_code == 200:
                        try:
                            return resp.json()["choices"][0]["message"]["content"] or ""
                        except (KeyError, IndexError, ValueError):
                            raise PriceServiceError("http", "beklenmeyen cevap biçimi")
                    if resp.status_code in (401, 403):
                        raise PriceServiceError("auth", f"HTTP {resp.status_code}: API anahtarı geçersiz/yetkisiz")
                    if resp.status_code == 402:
                        raise PriceServiceError("quota", "HTTP 402: Perplexity bakiyesi/kotası bitmiş")
                    if resp.status_code == 429:
                        last = PriceServiceError("rate", "HTTP 429: istek limiti")
                    elif resp.status_code >= 500:
                        last = PriceServiceError("http", f"HTTP {resp.status_code}")
                    else:
                        raise PriceServiceError("http", f"HTTP {resp.status_code}: {resp.text[:120]}")
                if attempt < 2:
                    await asyncio.sleep(self._retry_sleep * (attempt + 1))
            raise last or PriceServiceError("http", "bilinmeyen hata")
        finally:
            if owns_client:
                await client.aclose()
