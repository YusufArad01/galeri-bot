"""Sürüm 3 - Karşılaştırma modu (Perplexity'siz).

Her turda 40 yeni ilan taranır, filtrelerden (hasar, yaş/fiyat) geçenler arabam'daki aynı
donanım/motor/modeldeki güncel ilanların fiyat ortasıyla kıyaslanır. O turun en avantajlı
ilan(lar)ı (varsayılan 1) Telegram'a gider; yeterince ucuz ilan yoksa mesaj gitmez.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.benchmark import Benchmark
from src.modes.market import MIN_VALID_PRICE, discount_pct, fmt_pct, fmt_tl, listing_block

SUSPICIOUS_PCT = 40.0


@dataclass(frozen=True)
class CompareConfig:
    min_discount_pct: float = 10.0
    max_items: int = 1


@dataclass(frozen=True)
class Entry:
    data: dict
    median: float
    comparables: int
    level: str
    discount_pct: float


async def evaluate(data: dict, bench: Benchmark) -> Entry | None:
    """None = karşılaştırılamadı. Sayfa hakkı dolarsa BenchBudget yükselir (ilan sonraya kalır)."""
    price = float(data["price"])
    if price < MIN_VALID_PRICE or not data.get("year"):
        return None
    res = await bench.stats(data)
    if res is None:
        return None
    return Entry(data, res.median, res.comparables, res.level, discount_pct(price, res.median))


def pick_best(entries: list[Entry], cfg: CompareConfig) -> list[Entry]:
    ok = [e for e in entries if e.discount_pct >= cfg.min_discount_pct]
    ok.sort(key=lambda e: e.discount_pct, reverse=True)
    return ok[: cfg.max_items]


def format_entry(e: Entry, scanned: int) -> str:
    d = e.data
    warn = ""
    if e.discount_pct >= SUSPICIOUS_PCT:
        warn = "\n⚠️ Çok yüksek fark: kilometre/donanım farkı veya fiyat hatası olabilir, ilanı iyi kontrol edin."
    return (
        f"🏆 Bu turun en avantajlı ilanı — benzerlerinden %{fmt_pct(e.discount_pct)} ucuz\n\n"
        f"{listing_block(d)}\n\n"
        f"💰 İlan: {fmt_tl(d['price'])} TL\n"
        f"📊 {e.level} {e.comparables} güncel ilanın ortası: {fmt_tl(e.median)} TL\n"
        f"📉 Fark: {fmt_tl(e.median - float(d['price']))} TL"
        f"{warn}\n\n"
        f"(Bu turda {scanned} ilan karşılaştırıldı)\n🔗 {d['url']}"
    )
