"""İnternet taraması (Perplexity) ile fiyat tahmini: arabam'da yeterli aynı donanım emsali olmayan araçlar için YEDEK.

25.10.2026 ölçümü (arabam emsal ortancasına karşı, 2016-2020 model 13 araç): cevap verdiğinde tipik hata %5-8 ve
hiçbirinde %17'den fazla yüksek değildi; bilmediğinde boş döner (~%50). İlan satırı vermez, yalnız özet tahmin;
nadir araçlarda yuvarlak sayılar (tahmin gibi). Bu yüzden yalnız yedek: karar eşiği benchmark/compare/dealer'da sıkı tutulur.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.messages import vehicle_line
from src.models.domain import EstimateCache
from src.parsing import alnum_key

API_URL = "https://api.perplexity.ai/chat/completions"
MIN_PLAUSIBLE_TL = 50_000
MAX_PLAUSIBLE_TL = 20_000_000
KM_BUCKET = 25_000
UNKNOWN_TTL = timedelta(days=1)   # "bilmiyorum" cevabı kısa tutulur: aynı araç için her turda para harcanmasın, ama sonsuza dek de unutulmasın
BREAKER_LIMIT = 3                 # üst üste bu kadar altyapı hatasında bu turda çağrı yapılmaz


class PriceServiceError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind   # auth | quota | rate | http | network


def parse_average(text: str) -> float | None:
    """{"ortalama": <TL>} -> TL. Boş/0/anlamsız ya da makul aralık dışı ise None (= tahmin yok)."""
    m = re.search(r"\{.*?\}", re.sub(r"\[\d+\]", "", text or ""), re.DOTALL)
    if not m:
        return None
    try:
        raw = json.loads(m.group()).get("ortalama")
    except ValueError:
        return None
    if isinstance(raw, str):
        raw = re.sub(r"[^\d]", "", raw)   # "850.000" / "850,000 TL"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if MIN_PLAUSIBLE_TL <= value <= MAX_PLAUSIBLE_TL else None


def cache_key(data: dict) -> str:
    return "|".join([alnum_key(data.get("brand")), alnum_key(data.get("model")), alnum_key(data.get("trim")),
                     str(data.get("year")), str((data.get("km") or 0) // KM_BUCKET)])


class PriceService:
    def __init__(self, session: AsyncSession, api_key: str, model: str = "sonar-pro", cache_days: int = 7,
                 max_calls: int = 15, client: httpx.AsyncClient | None = None, retry_sleep: float = 8.0):
        self.session = session
        self.api_key = api_key
        self.model = model
        self.cache_ttl = timedelta(days=cache_days)
        self.max_calls = max_calls
        self._client = client
        self._retry_sleep = retry_sleep
        self._memo: dict[str, float | None] = {}
        self.calls = 0                  # bu örnekte (= bu turda) yapılan API çağrısı
        self.consecutive_failures = 0
        self.last_error_kind = ""

    async def estimate(self, data: dict) -> float | None:
        """Tahmini piyasa ortalaması (TL); bilinmiyorsa, kapalıysa ya da servis hatasındaysa None."""
        if not self.api_key or not data.get("year"):
            return None
        key = cache_key(data)
        if key in self._memo:
            return self._memo[key]
        cached = (await self.session.execute(select(EstimateCache).where(EstimateCache.cache_key == key))).scalars().first()
        if cached and cached.fetched_at >= datetime.utcnow() - (self.cache_ttl if cached.average > 0 else UNKNOWN_TTL):
            self._memo[key] = cached.average or None
            return self._memo[key]
        if self.calls >= self.max_calls or self.consecutive_failures >= BREAKER_LIMIT:
            return None   # bu turda hak doldu / servis bozuk; sonraki turda yeniden denenir

        self.calls += 1
        try:
            text = await self._ask(data)
        except PriceServiceError as exc:
            self.consecutive_failures += 1
            self.last_error_kind = exc.kind
            logger.warning(f"Fiyat tahmini alınamadı ({vehicle_line(data)} {data.get('year')}): {exc}")
            return None
        self.consecutive_failures = 0
        value = parse_average(text)
        if cached:
            cached.average, cached.fetched_at = value or 0.0, datetime.utcnow()
        else:
            self.session.add(EstimateCache(cache_key=key, average=value or 0.0))
        await self.session.commit()
        self._memo[key] = value
        logger.info(f"Fiyat tahmini {vehicle_line(data)} {data.get('year')} {data.get('km')}km: {value or 'yok'}")
        return value

    async def _ask(self, data: dict) -> str:
        km = data.get("km") or 0
        km_txt = f"{km:,}".replace(",", ".") if km else "bilinmiyor"
        user = (
            f"Türkiye ikinci el otomobil piyasasında şu an satışta olan {vehicle_line(data)}, {data['year']} model, yaklaşık {km_txt} km, "
            "ağır hasarsız ve normal bakımlı bir aracın güncel ilan fiyatı ortalaması kaç TL? "
            "Aynı seri, motor/donanım ve benzer kilometredeki güncel arabam.com ve sahibinden.com ilanlarını baz al. "
            'Yalnızca şu JSON\'u döndür: {"ortalama": <TL, tam sayı>, "min": <TL>, "max": <TL>}'
        )
        payload = {
            "model": self.model,
            "web_search_options": {"search_context_size": "high"},   # ölçümde cevap oranını artırdı
            "messages": [
                {"role": "system", "content": "Türkiye ikinci el araç piyasası fiyat analistisin. Yalnızca JSON döndür, açıklama yazma."},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient(timeout=90.0)
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
                        raise PriceServiceError("quota", "HTTP 402: Perplexity bakiyesi bitmiş")
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
            if self._client is None:
                await client.aclose()
