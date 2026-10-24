"""Karşılaştırma verisi: aynı donanım/motor/modeldeki güncel arabam ilanlarının fiyatları.

Tek liste sayfası ~20 ilan verir. Sayfalar önbelleğe alınır; bir döngüde en fazla
BENCH_MAX_FETCH_PER_CYCLE yeni sayfa açılır (siteye az istek = engel riski az).
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.domain import BenchCache
from src.parsing import tokens

MIN_PRICE = 50_000.0
LEVELS = ["Aynı donanımdaki", "Aynı motordaki", "Aynı modeldeki"]  # bench_urls sırasıyla (özelden genele)
ESTIMATE_LEVEL = "İnternet taraması tahmini"


class BenchBudget(Exception):
    """Bu döngünün yeni sayfa hakkı doldu; ilan kaydedilmez, sonraki döngüde denenir."""

    def __init__(self):
        super().__init__("bu döngüde açılabilecek karşılaştırma sayfası sınırı doldu")


@dataclass(frozen=True)
class BenchConfig:
    min_comparables: int = 6
    levels: int = 2   # kaç düzey kullanılsın: 1=donanım, 2=+motor, 3=+tüm model (gevşek)
    year_tolerance: int = 1
    km_tolerance_pct: float = 30.0
    # Fiyat etkileri (%), 23.10.2026'da 50 model sayfasında km ve yıl birlikte regresyonla ölçüldü:
    # 10.000 km başına ~%1,5 düşüş, her yeni yıl için ~%3,6 artış.
    km_adjust_pct_per_10k: float = 1.5
    year_adjust_pct_per_year: float = 3.5
    ttl_hours: float = 6.0
    max_fetch_per_cycle: int = 8
    # İnternet taraması tahmini (yedek) kararı: ilan tahminden en az bu kadar, en çok şu kadar ucuz olmalı.
    # Üst sınır: aşırı ucuz görünmek çoğunlukla tahminin hatalı olduğunu gösterir (eski araçlarda ölçümde uç değerler çıktı).
    est_min_discount_pct: float = 25.0
    est_max_discount_pct: float = 40.0


@dataclass(frozen=True)
class BenchResult:
    median: float
    comparables: int
    level: str
    url: str
    p25: float = 0.0   # emsallerin en ucuz çeyreğinin sınırı (km/yıl düzeltmeli); 0 = bilinmiyor
    estimated: bool = False   # True: arabam emsali yetmedi, değer internet taraması tahmini (median = tahmin)


def filtered_url(url: str, year: int, km: int, year_tol: int, km_pct: float) -> str:
    """Liste adresine arabam'ın yıl/km süzgecini ekler: sayfadaki ~20 ilanın hepsi benzer olur
    (süzgeçsiz sayfa ortalama 7 farklı yıldan ilan karıştırır, ±1 yıla az ilan düşer).
    Km aralığı 10 bine yuvarlanır ki yakın km'li ilanlar aynı önbellek sayfasını paylaşsın."""
    q = f"minYear={year - year_tol}&maxYear={year + year_tol}"
    if km:
        span = max(km * km_pct / 100.0, 15_000)
        q += f"&minkm={max(0, int((km - span) // 10_000) * 10_000)}&maxkm={math.ceil((km + span) / 10_000) * 10_000}"
    return url + ("&" if "?" in url else "?") + q


def _quantile(values: list[float], q: float) -> float:
    vals = sorted(values)
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def _km_close(a: int, b: int, pct: float) -> bool:
    if not a or not b:
        return True
    return abs(a - b) <= max(a * pct / 100.0, 15_000)


class Benchmark:
    def __init__(self, session: AsyncSession, scraper, cfg: BenchConfig, estimator=None):
        self.session = session
        self.scraper = scraper
        self.cfg = cfg
        self.estimator = estimator   # src.estimate.PriceService; yalnız arabam'da yeterli emsal yoksa sorulur
        self.fetched = 0       # bu döngüde açılan sayfa
        self.cache_hits = 0
        self.empty_pages = 0   # sayfa açıldı ama satır gelmedi (engel/yapı değişikliği belirtisi)
        self._memo: dict[str, list[dict]] = {}

    async def rows(self, url: str) -> list[dict]:
        if url in self._memo:
            return self._memo[url]
        cached = (await self.session.execute(select(BenchCache).where(BenchCache.cache_key == url))).scalars().first()
        if cached and cached.fetched_at >= datetime.utcnow() - timedelta(hours=self.cfg.ttl_hours):
            self.cache_hits += 1
            self._memo[url] = json.loads(cached.payload)
            return self._memo[url]
        if self.fetched >= self.cfg.max_fetch_per_cycle:
            raise BenchBudget()
        self.fetched += 1
        rows = await self.scraper.fetch_list_rows(url)
        if not rows:
            self.empty_pages += 1
            logger.warning(f"Karşılaştırma sayfası boş geldi: {url}")
            self._memo[url] = []
            return []
        payload = json.dumps(rows, ensure_ascii=False)
        if cached:
            cached.payload, cached.fetched_at = payload, datetime.utcnow()
        else:
            self.session.add(BenchCache(cache_key=url, payload=payload))
        await self.session.commit()
        self._memo[url] = rows
        return rows

    def _adjust(self, price: float, peer_km: int, km: int, peer_year: int = 0, year: int = 0) -> float:
        """Benzer ilanın fiyatını kıyaslanan aracın kilometresine ve yılına göre düzeltir: daha çok
        yürümüş ya da daha eski benzer doğal olarak ucuzdur; düzeltilmezse çok km yapmış/eski araçlar
        sahte 'fırsat' görünür. Toplam düzeltme ±%30 ile sınırlı."""
        factor = 1.0
        if km and peer_km:
            factor += self.cfg.km_adjust_pct_per_10k / 100.0 * (peer_km - km) / 10_000
        if year and peer_year:
            factor += self.cfg.year_adjust_pct_per_year / 100.0 * (year - peer_year)
        return price * min(1.3, max(0.7, factor))

    async def stats(self, data: dict, allow_estimate: bool = True) -> BenchResult | None:
        """En özel sayfadan başlayıp yeterli benzer ilan bulunan ilk düzeyin medyanı. Hiçbir düzeyde yeterli
        emsal yoksa (ve site engelli değilse) internet taraması tahmini (estimated=True) döner; o da yoksa None."""
        year, km = data.get("year") or 0, data.get("km") or 0
        trim = tokens(data.get("trim"))
        for level, url in list(zip(LEVELS, data.get("bench_urls") or []))[: self.cfg.levels]:
            if year:
                url = filtered_url(url, year, km, self.cfg.year_tolerance, self.cfg.km_tolerance_pct)
            comps = [
                self._adjust(r["price"], r["km"], km, r["year"], year) for r in await self.rows(url)
                if r["id"] != data["source_listing_id"] and r["price"] >= MIN_PRICE
                # motor düzeyinde farklı donanımlar karışır (Ambition ile Style arası %20+ fark): yalnız aynı donanım adı
                and (not trim or trim <= tokens(r.get("name")))
                and abs(r["year"] - year) <= self.cfg.year_tolerance
                and _km_close(km, r["km"], self.cfg.km_tolerance_pct)
            ]
            if len(comps) >= self.cfg.min_comparables:
                return BenchResult(float(statistics.median(comps)), len(comps), level, url, _quantile(comps, 0.25))
        # Engelde liste sayfaları boş gelir: bu "nadir araç" değil "bilinmiyor" demektir, tahmin sorulmaz
        if allow_estimate and self.estimator is not None and not getattr(self.scraper, "last_blocked", False):
            avg = await self.estimator.estimate(data)
            if avg:
                return BenchResult(float(avg), 0, ESTIMATE_LEVEL, "", 0.0, estimated=True)
        return None
