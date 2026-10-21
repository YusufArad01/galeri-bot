"""Sürüm 2 - Galerici modu.

Her galericinin uzman olduğu marka/model(ler) ve hedef fiyatları bir profil dosyasında tutulur
(dealers.json veya DEALERS_JSON ortam değişkeni). Kurallara uyan ve hedef fiyatın altındaki
ilanlar galericinin kendi Telegram sohbetine gönderilir. Galerici isterse ayrıca
`market_check` ile Perplexity piyasa ortalamasının %X altı da fırsat sayılır.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from src.modes.market import Candidate, discount_pct, fmt_pct, fmt_tl, listing_block
from src.parsing import alnum_key, tokens
from src.pricing.perplexity import PriceEstimate, PriceService, PriceServiceError


class DealerConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    brand: str
    model: str = ""
    year_min: int | None = None
    year_max: int | None = None
    km_max: int | None = None
    max_price: float | None = None
    search_url: str | None = None

    def label(self) -> str:
        return f"{self.brand} {self.model}".strip()

    def matches(self, data: dict) -> bool:
        title = data.get("title", "")
        brand_key = alnum_key(self.brand)
        if brand_key != alnum_key(data.get("brand")) and brand_key not in alnum_key(title):
            return False
        if self.model:
            haystack = tokens(f"{data.get('model', '')} {data.get('trim', '')} {title}")
            if not tokens(self.model) <= haystack:
                return False
        year = data.get("year") or 0
        if self.year_min and year < self.year_min:
            return False
        if self.year_max and year > self.year_max:
            return False
        km = data.get("km") or 0
        if self.km_max and km and km > self.km_max:
            return False
        return True


@dataclass(frozen=True)
class Dealer:
    id: str
    name: str
    chat_id: str
    rules: tuple[Rule, ...]
    market_check: bool = False
    market_discount_pct: float = 15.0
    allow_damaged: bool = False
    active: bool = True


def _num(value, field_name: str, ctx: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise DealerConfigError(f"{ctx}: '{field_name}' sayı olmalı (gelen: {value!r})")


def parse_dealers(raw: dict | list) -> list[Dealer]:
    items = raw.get("dealers") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise DealerConfigError("profil dosyası {'dealers': [...]} biçiminde olmalı")
    dealers: list[Dealer] = []
    seen: set[str] = set()
    for i, d in enumerate(items, 1):
        did = str(d.get("id") or "").strip()
        ctx = f"galerici #{i} ({did or '?'})"
        if not did:
            raise DealerConfigError(f"{ctx}: 'id' zorunlu")
        if did in seen:
            raise DealerConfigError(f"{ctx}: 'id' tekrar ediyor")
        seen.add(did)
        market_check = bool(d.get("market_check", False))
        rules = []
        for j, r in enumerate(d.get("rules") or [], 1):
            rctx = f"{ctx} kural #{j}"
            if not str(r.get("brand") or "").strip():
                raise DealerConfigError(f"{rctx}: 'brand' zorunlu")
            max_price = _num(r.get("max_price"), "max_price", rctx)
            if max_price is None and not market_check:
                raise DealerConfigError(f"{rctx}: 'max_price' yok ve galericide market_check kapalı; kural hiçbir şey yakalamaz")
            rules.append(Rule(
                brand=str(r["brand"]).strip(),
                model=str(r.get("model") or "").strip(),
                year_min=int(r["year_min"]) if r.get("year_min") else None,
                year_max=int(r["year_max"]) if r.get("year_max") else None,
                km_max=int(r["km_max"]) if r.get("km_max") else None,
                max_price=max_price,
                search_url=(r.get("search_url") or None),
            ))
        if not rules:
            raise DealerConfigError(f"{ctx}: en az bir kural gerekli")
        dealers.append(Dealer(
            id=did,
            name=str(d.get("name") or did),
            chat_id=str(d.get("chat_id") or "").strip(),
            rules=tuple(rules),
            market_check=market_check,
            market_discount_pct=float(d.get("market_discount_pct", 15.0)),
            allow_damaged=bool(d.get("allow_damaged", False)),
            active=bool(d.get("active", True)),
        ))
    return dealers


def load_dealers(json_env: str = "", file_path: str = "dealers.json") -> list[Dealer]:
    """DEALERS_JSON doluysa onu, değilse dosyayı okur. Dosya yoksa boş liste."""
    if json_env.strip():
        raw = json.loads(json_env)
    else:
        path = Path(file_path)
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
    return [d for d in parse_dealers(raw) if d.active]


def search_urls(dealers: list[Dealer]) -> list[str]:
    """Kurallarda tanımlı özel arama sayfaları (tekrarsız, sıralı)."""
    seen: dict[str, None] = {}
    for d in dealers:
        for r in d.rules:
            if r.search_url:
                seen.setdefault(r.search_url, None)
    return list(seen)


async def evaluate(data: dict, dealers: list[Dealer], prices: PriceService,
                   fallback_chat_id: str) -> tuple[list[Candidate], PriceServiceError | None]:
    """Her galerici için en fazla bir bildirim üretir: (bildirimler, ertelenen_hata).

    Perplexity yalnızca kurala uyan + market_check açık galericilerde çağrılır. Hedef fiyat
    zaten tetiklenmişse piyasa servisi çökse bile o galericinin bildirimi üretilir. Servis
    hatası yüzünden değerlendirilemeyen galerici varsa hata ayrıca döner; çağrıcı, başarılı
    bildirimleri kuyruğa alıp ilanı sonraki döngüde yeniden değerlendirir (mükerrer bildirim
    alert tablosundaki benzersizlik kısıtıyla engellenir).
    """
    price = float(data["price"])
    out: list[Candidate] = []
    deferred: PriceServiceError | None = None
    for dealer in dealers:
        if data.get("is_heavy_damage") and not dealer.allow_damaged:
            continue
        matched = [r for r in dealer.rules if r.matches(data)]
        if not matched:
            continue

        target_hits = [r for r in matched if r.max_price is not None and price <= r.max_price]

        est: PriceEstimate | None = None
        market_hit = False
        market_err: PriceServiceError | None = None
        if dealer.market_check:
            try:
                est = await prices.estimate(data["brand"], data["model"], data.get("trim", ""),
                                            data["year"], data.get("km") or 0)
            except PriceServiceError as exc:
                if not target_hits:
                    deferred = deferred or exc
                    continue
                market_err = exc
            if est and est.average > 0:
                market_hit = discount_pct(price, est.average) >= dealer.market_discount_pct

        if not target_hits and not market_hit:
            continue

        lines = [f"🏪 {dealer.name} — uygun ilan bulundu", "", listing_block(data), "", f"💰 İlan: {fmt_tl(price)} TL"]
        reasons = []
        for r in target_hits[:2]:
            lines.append(f"🎯 Hedef: {r.label()} ≤ {fmt_tl(r.max_price)} TL → {fmt_tl(r.max_price - price)} TL altında")
            reasons.append(f"hedef {r.label()} ≤ {fmt_tl(r.max_price)}")
        if est and est.average > 0:
            pct = discount_pct(price, est.average)
            tag = "✅" if market_hit else "ℹ️"
            lines.append(f"📊 {tag} Piyasa ort.: {fmt_tl(est.average)} TL (ilan %{fmt_pct(pct)} {'altında' if pct >= 0 else 'üstünde'})")
            if market_hit:
                reasons.append(f"piyasa -%{fmt_pct(pct)}")
        elif market_err is not None:
            lines.append("📊 Piyasa ortalaması şu an alınamadı.")
        if data.get("is_heavy_damage"):
            lines.append("⚠️ Hasar/boya kaydı bulunan ilan: " + ", ".join(data.get("damage_reasons") or ["kontrol edin"]))
        lines += ["", f"🔗 {data['url']}"]

        chat_id = dealer.chat_id or fallback_chat_id
        if not dealer.chat_id:
            lines.insert(0, f"[{dealer.id}: chat_id tanımlı değil, yönetici sohbetine gönderildi]")
            logger.warning(f"Galerici {dealer.id} için chat_id yok; bildirim yönetici sohbetine gidiyor.")
        out.append(Candidate("dealer", dealer.id, chat_id, " + ".join(reasons), "\n".join(lines)))
    return out, deferred
