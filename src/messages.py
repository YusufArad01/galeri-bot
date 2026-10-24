"""Mesaj biçimlendirme yardımcıları ve gönderilecek bildirimin veri sınıfı."""
from __future__ import annotations

from dataclasses import dataclass

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
