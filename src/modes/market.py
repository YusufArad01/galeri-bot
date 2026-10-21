"""Sürüm 1 - Piyasa modu: ilan fiyatı Perplexity piyasa ortalamasının %X altındaysa fırsat."""
from __future__ import annotations

from dataclasses import dataclass

from src.pricing.perplexity import PriceEstimate, PriceService
from src.parsing import alnum_key

MIN_VALID_PRICE = 50_000.0  # bunun altı büyük ihtimalle ayrıştırma hatası


@dataclass
class Candidate:
    """Gönderilecek bir bildirim."""
    mode: str
    dealer_id: str
    chat_id: str
    reason: str
    text: str


def fmt_tl(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{int(round(value)):,}".replace(",", ".")


def fmt_pct(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def vehicle_line(data: dict) -> str:
    model, trim = data.get("model", ""), data.get("trim", "")
    if trim and alnum_key(trim).startswith(alnum_key(model)):  # "A" + "A 180 Urban" -> tek kez yaz
        model = ""
    parts = [data.get("brand", ""), model, trim]
    return " ".join(p for p in parts if p and p != "Bilinmiyor").strip()


def listing_block(data: dict) -> str:
    km = data.get("km") or 0
    km_txt = f"{fmt_tl(km)} km" if km else "km ?"
    return (
        f"{vehicle_line(data)}\n"
        f"📅 {data.get('year') or '?'}  •  🛣️ {km_txt}  •  📍 {data.get('city') or '?'}\n"
        f"👤 {data.get('seller_type') or '?'}"
    )


def discount_pct(price: float, average: float) -> float:
    return (average - price) / average * 100.0


async def evaluate(data: dict, prices: PriceService, chat_id: str,
                   threshold_pct: float, suspicious_pct: float) -> Candidate | None:
    """None = fırsat değil. Fiyat servisi çökerse PriceServiceError yükselir (çağıran kaydı atlamamalı)."""
    price = float(data["price"])
    if price < MIN_VALID_PRICE or not data.get("year"):
        return None

    est: PriceEstimate | None = await prices.estimate(
        data["brand"], data["model"], data.get("trim", ""), data["year"], data.get("km") or 0
    )
    if est is None or est.average <= 0:
        return None

    pct = discount_pct(price, est.average)
    if pct < threshold_pct:
        return None

    warn = ""
    if pct >= suspicious_pct:
        warn = "\n⚠️ Çok yüksek indirim: model/donanım eşleşmesi veya fiyat hatası olabilir, ilanı iyi kontrol edin."
    text = (
        f"🚗 FIRSAT — piyasanın %{fmt_pct(pct)} altında\n\n"
        f"{listing_block(data)}\n\n"
        f"💰 İlan: {fmt_tl(price)} TL\n"
        f"📊 Piyasa ort.: {fmt_tl(est.average)} TL"
        + (f" ({fmt_tl(est.low)} – {fmt_tl(est.high)})" if est.low and est.high else "")
        + f"\n📉 Fark: {fmt_tl(est.average - price)} TL"
        f"{warn}\n\n🔗 {data['url']}"
    )
    return Candidate("market", "", chat_id, f"piyasa -%{fmt_pct(pct)}", text)
