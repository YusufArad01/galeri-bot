"""Karşılaştırma modu.

Her turda yeni ilanlar filtrelerden (hasar, yaş/fiyat) geçirilir ve arabam'daki aynı
donanımdaki güncel ilanların fiyat ortasıyla kıyaslanır (yeterli emsal yoksa yedek: internet taraması
tahmini, sıkı eşikle ve "tahmin" etiketiyle). Eşiği geçenler aday havuzuna girer. Mesaj bombardımanı olmasın diye havuzdan düzenli aralıkla (varsayılan saatte
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
from src.messages import MIN_VALID_PRICE, discount_pct, fmt_pct, fmt_tl, listing_block
from src.notifications import enqueue_alert

# Boya/değişen payı (puan): hasarlı ilan doğal olarak ucuzdur. 25.10.2026'da 139 ilanla ölçüldü (aynı donanım içinde,
# yıl ve km sabit): her değişen parça -%6,2 (%95 aralık 4,7-7,7), her boyalı/lokal boyalı parça -%1,4 (0,8-2,0).
# Ceza hasarsızdan başlar (emsallerin %71'inde de boya/değişen var, yani biraz temkinli).
PENALTY_PER_DEGISEN = 6.0
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
    estimated: bool = False    # True: median arabam emsali değil, internet taraması tahmini (eşik evaluate'te uygulandı)


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
    if res.p25 and price > res.p25:   # ortancadan ucuz görünse de emsallerin en ucuz çeyreğinde değilse fırsat sayılmaz (çift tepeli fiyat dağılımı)
        return None
    raw = discount_pct(price, res.median)
    penalty = PENALTY_PER_DEGISEN * (data.get("degisen") or 0) + PENALTY_PER_BOYALI * (data.get("boyali") or 0)
    entry = Entry(data, res.median, res.comparables, res.level, raw - penalty, raw, res.estimated)
    if res.estimated and not bench.cfg.est_min_discount_pct <= entry.discount_pct <= bench.cfg.est_max_discount_pct:
        return None   # tahmin yalnız belirgin farkta (varsayılan %25-%40) fırsat sayılır
    return entry


def score(e: Entry) -> float:
    """Sıralama puanı: fark büyüdükçe artar, ama SUSPICIOUS_PCT'yi aşınca düşer (aşırı ucuz = şüpheli).
    Tahmine dayalı adaylar doğrulanmış arabam emsalinden sonra gelsin diye puanı %25 düşük sayılır."""
    d = e.discount_pct
    base = d if d <= SUSPICIOUS_PCT else max(0.0, 2 * SUSPICIOUS_PCT - d)
    return base * 0.75 if e.estimated else base


def damage_line(d: dict, penalty_pts: float = 0.0) -> str:
    parts = []
    if d.get("degisen"):
        parts.append(f"{d['degisen']} değişen")
    if d.get("boyali"):
        parts.append(f"{d['boyali']} boyalı")
    if not parts:
        return "\n🔧 Boya/değişen: yok"
    return f"\n🔧 Boya/değişen: {', '.join(parts)} (hasar payı %{fmt_pct(penalty_pts)} referanstan düşüldü)"


def reference_view(e: Entry) -> tuple[float, float, float, float]:
    """(hasar payı düşülmüş referans, fark TL, farkın referansa oranı %, hasar payı puanı). Mesajdaki bütün sayılar
    buradan çıkar ki birbiriyle tutarlı olsun (eşik/sıralama ise ortancaya göre net fark olan discount_pct ile yapılır)."""
    penalty = max(0.0, e.raw_discount_pct - e.discount_pct)
    ref = e.median * (1 - penalty / 100.0)
    gap = ref - float(e.data["price"])
    return ref, gap, (gap / ref * 100.0 if ref > 0 else 0.0), penalty


def format_entry(e: Entry, pool: int, fresh_hours: float) -> str:
    d = e.data
    ref, gap, pct, penalty = reference_view(e)
    if e.estimated:
        return (
            f"🔮 Tahmini fiyata göre %{fmt_pct(pct)} ucuz (kesin değil)\n\n"
            f"{listing_block(d)}\n\n"
            f"💰 İlan: {fmt_tl(d['price'])} TL\n"
            f"🔮 İnternet taraması tahmini: {fmt_tl(e.median)} TL (arabam'da kıyaslanacak yeterli aynı donanım yok)"
            f"{damage_line(d, penalty)}\n"
            f"📉 Referans: {fmt_tl(ref)} TL → ilan {fmt_tl(gap)} TL (%{fmt_pct(pct)}) altında\n"
            f"⚠️ Bu bir tahmindir, fiyat doğrulanamadı: ilanı iyi kontrol edin.\n\n"
            f"(Son {fresh_hours:g} saatte {pool} aday arasından en iyisi)\n🔗 {d['url']}"
        )
    warn = ""
    if e.discount_pct >= SUSPICIOUS_PCT:
        warn = "\n⚠️ Çok yüksek fark: kilometre/donanım farkı veya fiyat hatası olabilir, ilanı iyi kontrol edin."
    return (
        f"🏆 Seçilen ilan — benzerlerinden %{fmt_pct(pct)} ucuz\n\n"
        f"{listing_block(d)}\n\n"
        f"💰 İlan: {fmt_tl(d['price'])} TL\n"
        f"📊 {e.level} {e.comparables} güncel ilanın ortası: {fmt_tl(e.median)} TL"
        f"{damage_line(d, penalty)}\n"
        f"📉 Referans: {fmt_tl(ref)} TL → ilan {fmt_tl(gap)} TL (%{fmt_pct(pct)}) altında"
        f"{warn}\n\n"
        f"(Son {fresh_hours:g} saatte {pool} aday arasından en iyisi)\n🔗 {d['url']}"
    )


# --------------------------------------------------------------------------- #
# Aday havuzu ve düzenli gönderim
# --------------------------------------------------------------------------- #
def passes(e: Entry, min_discount: float, max_discount: float = 0.0, min_profit_tl: int = 0,
           min_profit_pct: float = 0.0) -> bool:
    """Yüzde eşiği YA DA (TL kâr eşiği VE alt yüzde); her iki durumda da üst sınırın altında.
    Tahmine dayalı adayın eşiği evaluate'te (tahmin penceresi) uygulandı."""
    if e.estimated:
        return True
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
                              "raw_discount_pct": e.raw_discount_pct, "estimated": e.estimated},
                             ensure_ascii=False)
        session.add(CompareCandidate(source_listing_id=sid, score=score(e), payload=payload))
        added += 1
    await session.commit()
    return added


def _from_payload(raw: str) -> Entry:
    p = json.loads(raw)
    return Entry(p["data"], p["median"], p["comparables"], p["level"], p["discount_pct"],
                 p.get("raw_discount_pct", p["discount_pct"]), p.get("estimated", False))


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
            reason=f"{'tahmin' if entry.estimated else 'benzerlerinden'} -%{entry.discount_pct:.1f}")   # "tahmin" oy sayımını ayırır
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
