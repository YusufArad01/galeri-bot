"""Karşılaştırma verisi: aynı donanım/motor/modeldeki güncel arabam ilanlarının fiyatları.

Tek liste sayfası ~20 ilan verir. Sayfalar önbelleğe alınır; bir döngüde en fazla
BENCH_MAX_FETCH_PER_CYCLE yeni sayfa açılır (siteye az istek = engel riski az).
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.domain import BenchCache
from src.pricing.perplexity import PriceServiceError

MIN_PRICE = 50_000.0
LEVELS = ["Aynı donanımdaki", "Aynı motordaki", "Aynı modeldeki"]  # bench_urls sırasıyla (özelden genele)


class BenchBudget(PriceServiceError):
    """Bu döngünün yeni sayfa hakkı doldu; ilan kaydedilmez, sonraki döngüde denenir."""

    def __init__(self):
        super().__init__("bench_budget", "bu döngüde açılabilecek karşılaştırma sayfası sınırı doldu")


@dataclass(frozen=True)
class BenchConfig:
    min_comparables: int = 5
    year_tolerance: int = 1
    km_tolerance_pct: float = 40.0
    # 10.000 km başına fiyat düşüşü (%). 22.10.2026'da 34 model/yıl grubunda ölçüldü: medyan ~%2.
    km_adjust_pct_per_10k: float = 2.0
    ttl_hours: float = 6.0
    max_fetch_per_cycle: int = 8


@dataclass(frozen=True)
class BenchResult:
    median: float
    comparables: int
    level: str
    url: str


def _km_close(a: int, b: int, pct: float) -> bool:
    if not a or not b:
        return True
    return abs(a - b) <= max(a * pct / 100.0, 15_000)


class Benchmark:
    def __init__(self, session: AsyncSession, scraper, cfg: BenchConfig):
        self.session = session
        self.scraper = scraper
        self.cfg = cfg
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

    def _adjust(self, price: float, peer_km: int, km: int) -> float:
        """Benzer ilanın fiyatını kıyaslanan aracın kilometresine göre düzeltir: daha çok yürümüş
        benzer doğal olarak ucuzdur, aksi halde çok km yapmış araçlar sahte 'fırsat' görünür."""
        if not km or not peer_km:
            return price
        factor = 1 + self.cfg.km_adjust_pct_per_10k / 100.0 * (peer_km - km) / 10_000
        return price * min(1.3, max(0.7, factor))

    async def stats(self, data: dict) -> BenchResult | None:
        """En özel sayfadan başlayıp yeterli benzer ilan bulunan ilk düzeyin medyanı."""
        year, km = data.get("year") or 0, data.get("km") or 0
        for level, url in zip(LEVELS, data.get("bench_urls") or []):
            comps = [
                self._adjust(r["price"], r["km"], km) for r in await self.rows(url)
                if r["id"] != data["source_listing_id"] and r["price"] >= MIN_PRICE
                and abs(r["year"] - year) <= self.cfg.year_tolerance
                and _km_close(km, r["km"], self.cfg.km_tolerance_pct)
            ]
            if len(comps) >= self.cfg.min_comparables:
                return BenchResult(float(statistics.median(comps)), len(comps), level, url)
        return None
