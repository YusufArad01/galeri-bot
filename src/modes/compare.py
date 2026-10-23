"""Sürüm 3 - Karşılaştırma modu (Perplexity'siz).

Her turda yeni ilanlar filtrelerden (hasar, yaş/fiyat) geçirilir ve arabam'daki aynı
donanım/motor/modeldeki güncel ilanların fiyat ortasıyla kıyaslanır. Eşiği geçenler aday
havuzuna girer. Mesaj bombardımanı olmasın diye havuzdan düzenli aralıkla (varsayılan saatte
bir) en iyi aday seçilip gönderilir; günlük üst sınır vardır (varsayılan 15).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.benchmark import Benchmark
from src.models.domain import BotState, CompareCandidate
from src.modes.market import MIN_VALID_PRICE, discount_pct, fmt_pct, fmt_tl, listing_block
from src.notifications import enqueue_alert

# Boya/değişen payı: benzer ilanlar çoğunlukla temiz, hasarlı ilan doğal olarak ucuz. Veriyle ölçülmedi, tahmini
# değerler (puan): bir değişen parça ~4, bir boyalı/lokal boyalı parça ~1,5 puan fark yaratır.
PENALTY_PER_DEGISEN = 4.0
PENALTY_PER_BOYALI = 1.5
SUSPICIOUS_PCT = 35.0   # bundan büyük fark çoğunlukla hata/dolandırıcılık/gizli hasar: puan düşer
TR_OFFSET = timedelta(hours=3)  # gün sınırı Türkiye saatine göre
_SHOWN = ("brand", "model", "trim", "year", "km", "city", "seller_type", "price", "url", "source_listing_id",
          "degisen", "boyali")


@dataclass(frozen=True)
class CompareConfig:
    min_discount_pct: float = 15.0
    max_items: int = 1            # her gönderimde en iyi kaç ilan
    release_minutes: int = 90     # iki gönderim arası en az süre: bu pencerede toplananlardan en iyisi seçilir
    fresh_hours: float = 3.0      # bundan eski aday gönderilmez (ilan satılmış olabilir)
    daily_max: int = 10
    max_discount_pct: float = 30.0  # bundan büyük fark çoğunlukla hata/dolandırıcılık/ucuz-işe yaramaz araç: aday olmaz (0 = sınır yok)
    # Yüzde eşiğinin yanında TL kâr yolu: pahalı araçta %15 beklemeden, kâr yeterliyse ve fark hata payının
    # (~%5) üstündeyse aday olur. 0 = bu yol kapalı.
    min_profit_tl: int = 150_000
    min_profit_pct: float = 8.0
    max_km: int = 200_000         # bunun üstündeki araçlar aday olmaz (0 = sınır yok)
    max_age_years: int = 12       # bundan eski model aday olmaz: alıcısı yok, galericiye faydası yok (0 = sınır yok)
    active_start: int = 8         # Türkiye saatiyle gönderim yapılan saatler [başlangıç, bitiş);
    active_end: int = 23          # gece mesajı günlük hakkı boşa harcamasın


@dataclass(frozen=True)
class Entry:
    data: dict
    median: float
    comparables: int
    level: str
    discount_pct: float        # boya/değişen payı düşülmüş fark: sıralama ve eşik bununla yapılır
    raw_discount_pct: float = 0.0


async def evaluate(data: dict, bench: Benchmark, max_km: int = 0, min_year: int = 0) -> Entry | None:
    """None = karşılaştırılamadı. Sayfa hakkı dolarsa BenchBudget yükselir (ilan sonraya kalır)."""
    price = float(data["price"])
    if price < MIN_VALID_PRICE or not data.get("year"):
        return None
    if min_year and data["year"] < min_year:
        return None
    if max_km and (data.get("km") or 0) > max_km:
        return None
    res = await bench.stats(data)
    if res is None:
        return None
    raw = discount_pct(price, res.median)
    penalty = PENALTY_PER_DEGISEN * (data.get("degisen") or 0) + PENALTY_PER_BOYALI * (data.get("boyali") or 0)
    return Entry(data, res.median, res.comparables, res.level, raw - penalty, raw)


def score(e: Entry) -> float:
    """Sıralama puanı: fark büyüdükçe artar, ama SUSPICIOUS_PCT'yi aşınca düşer (aşırı ucuz = şüpheli)."""
    d = e.discount_pct
    return d if d <= SUSPICIOUS_PCT else max(0.0, 2 * SUSPICIOUS_PCT - d)


def damage_line(d: dict) -> str:
    parts = []
    if d.get("degisen"):
        parts.append(f"{d['degisen']} değişen")
    if d.get("boyali"):
        parts.append(f"{d['boyali']} boyalı")
    return f"\n🔧 Boya/değişen: {', '.join(parts)} (farktan düşüldü)" if parts else "\n🔧 Boya/değişen: yok"


def format_entry(e: Entry, pool: int, fresh_hours: float) -> str:
    d = e.data
    warn = ""
    if e.discount_pct >= SUSPICIOUS_PCT:
        warn = "\n⚠️ Çok yüksek fark: kilometre/donanım farkı veya fiyat hatası olabilir, ilanı iyi kontrol edin."
    return (
        f"🏆 Seçilen ilan — benzerlerinden %{fmt_pct(e.discount_pct)} ucuz\n\n"
        f"{listing_block(d)}\n\n"
        f"💰 İlan: {fmt_tl(d['price'])} TL\n"
        f"📊 {e.level} {e.comparables} güncel ilanın ortası: {fmt_tl(e.median)} TL\n"
        f"📉 Fark: {fmt_tl(e.median - float(d['price']))} TL (%{fmt_pct(e.raw_discount_pct)})"
        f"{damage_line(d)}"
        f"{warn}\n\n"
        f"(Son {fresh_hours:g} saatte {pool} aday arasından en iyisi)\n🔗 {d['url']}"
    )


# --------------------------------------------------------------------------- #
# Aday havuzu ve düzenli gönderim
# --------------------------------------------------------------------------- #
def passes(e: Entry, min_discount: float, max_discount: float = 0.0, min_profit_tl: int = 0,
           min_profit_pct: float = 0.0) -> bool:
    """Yüzde eşiği YA DA (TL kâr eşiği VE alt yüzde); her iki durumda da üst sınırın altında."""
    if max_discount and e.discount_pct > max_discount:
        return False
    if e.discount_pct >= min_discount:
        return True
    return bool(min_profit_tl) and e.discount_pct >= min_profit_pct         and e.median * e.discount_pct / 100.0 >= min_profit_tl


async def store_candidates(session: AsyncSession, entries: list[Entry], min_discount: float,
                           max_discount: float = 0.0, min_profit_tl: int = 0, min_profit_pct: float = 0.0) -> int:
    """Eşiği geçenleri havuza ekler (aynı ilan iki kez eklenmez). Eklenen sayıyı döner."""
    added = 0
    for e in entries:
        if not passes(e, min_discount, max_discount, min_profit_tl, min_profit_pct):
            continue
        sid = e.data["source_listing_id"]
        if (await session.execute(select(CompareCandidate.id).where(CompareCandidate.source_listing_id == sid))).first():
            continue
        payload = json.dumps({"data": {k: e.data.get(k) for k in _SHOWN}, "median": e.median,
                              "comparables": e.comparables, "level": e.level, "discount_pct": e.discount_pct,
                              "raw_discount_pct": e.raw_discount_pct},
                             ensure_ascii=False)
        session.add(CompareCandidate(source_listing_id=sid, score=score(e), payload=payload))
        added += 1
    await session.commit()
    return added


def _from_payload(raw: str) -> Entry:
    p = json.loads(raw)
    return Entry(p["data"], p["median"], p["comparables"], p["level"], p["discount_pct"],
                 p.get("raw_discount_pct", p["discount_pct"]))


async def release(session: AsyncSession, cfg: CompareConfig, chat_id: str, now: datetime | None = None) -> int:
    """Zamanı geldiyse havuzdaki en iyi aday(lar)ı gönderim kuyruğuna alır. Kuyruğa alınan sayıyı döner."""
    now = now or datetime.utcnow()
    if not cfg.active_start <= (now + TR_OFFSET).hour < cfg.active_end:
        return 0
    state = (await session.execute(select(BotState).where(BotState.key == "compare_last_release"))).scalars().first()
    if state and now - datetime.fromisoformat(state.value) < timedelta(minutes=cfg.release_minutes):
        return 0

    fresh_cut = now - timedelta(hours=cfg.fresh_hours)
    await session.execute(update(CompareCandidate).where(
        CompareCandidate.status == "NEW", CompareCandidate.created_at < fresh_cut).values(status="EXPIRED"))

    day_start = (now + TR_OFFSET).replace(hour=0, minute=0, second=0, microsecond=0) - TR_OFFSET
    sent_today = (await session.execute(select(func.count()).select_from(CompareCandidate).where(
        CompareCandidate.status == "RELEASED", CompareCandidate.released_at >= day_start))).scalar_one()
    room = min(cfg.max_items, cfg.daily_max - sent_today)
    if room <= 0:
        await session.commit()
        return 0

    pool = (await session.execute(select(CompareCandidate).where(CompareCandidate.status == "NEW")
                                  .order_by(CompareCandidate.score.desc()))).scalars().all()
    queued = 0
    for cand in pool[:room]:
        entry = _from_payload(cand.payload)
        alert = await enqueue_alert(
            session, source_listing_id=cand.source_listing_id, mode="compare", dealer_id="",
            chat_id=chat_id, text=format_entry(entry, len(pool), cfg.fresh_hours),
            reason=f"benzerlerinden -%{entry.discount_pct:.1f}")
        cand.status, cand.released_at = "RELEASED", now
        if alert is not None:
            queued += 1
            logger.info(f"Karşılaştırma bildirimi kuyruğa alındı: -%{entry.discount_pct:.1f} -> {entry.data['url']}")
    if queued:
        if state:
            state.value = now.isoformat()
        else:
            session.add(BotState(key="compare_last_release", value=now.isoformat()))
    await session.commit()
    return queued
