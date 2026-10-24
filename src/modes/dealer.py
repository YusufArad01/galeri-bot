"""Sürüm 2 - Galerici modu.

Her galericinin uzman olduğu marka/model(ler) ve hedef fiyatları bir profil dosyasında tutulur
(dealers.json veya DEALERS_JSON ortam değişkeni). Kurallara uyan ve hedef fiyatın altındaki
ilanlar galericinin kendi Telegram sohbetine gönderilir. Galerici isterse ayrıca `market_check` ile
arabam'daki aynı donanımlı güncel ilanların ortasının altı da fırsat sayılır (emsal yoksa: tahmin yedeği, sıkı eşikle).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.benchmark import BenchBudget
from src.messages import Candidate, discount_pct, fmt_pct, fmt_tl, listing_block
from src.parsing import alnum_key, fold, tokens


class DealerConfigError(ValueError):
    pass


EAST = {"mardin", "diyarbakir", "sanliurfa", "batman", "siirt", "sirnak", "hakkari", "van", "bitlis", "mus", "bingol",
        "elazig", "malatya", "adiyaman", "gaziantep", "kilis", "erzurum", "erzincan", "kars", "agri", "igdir", "ardahan",
        "tunceli", "bayburt"}
METRO = {"istanbul", "ankara", "izmir", "bursa", "antalya", "kocaeli"}


@dataclass(frozen=True)
class PriceModel:
    """Galericinin verdiği km-fiyat bantları (temiz, büyükşehir aracı için) + düzeltmeler.
    Referans fiyat = bant merkezleri arası doğrusal ara değer, model yılı, boya, bölge ve donanım düzeltmeli."""
    ref_year: int
    bands: tuple[tuple[int, int, int, int], ...]     # (km_alt, km_üst, fiyat_alt, fiyat_üst), km artan sırada
    year_pct: float = 3.5                           # model yılı başına fiyat farkı (%)
    paint_part_tl: int = 0                          # boyalı parça başına düşüş
    east_tl: int = 0                                # Doğu/Güneydoğu illerinde indirim
    nonmetro_tl: int = 0                            # büyükşehir dışında indirim (Doğu da dahil)
    premiums: tuple[tuple[tuple[str, ...], int], ...] = ()   # (başlık sözcükleri, TL): geçerse referansa eklenir

    def expected(self, data: dict) -> float | None:
        km = data.get("km") or 0
        pts = [((lo + hi) / 2, (plo + phi) / 2) for lo, hi, plo, phi in self.bands]
        if not km or km < self.bands[0][0] - 40_000 or km > self.bands[-1][1] + 40_000 or len(pts) < 2:
            return None   # bantların çok dışında: model fikir vermez
        if km <= pts[0][0]:
            (x0, y0), (x1, y1) = pts[0], pts[1]
        elif km >= pts[-1][0]:
            (x0, y0), (x1, y1) = pts[-2], pts[-1]
        else:
            i = next(i for i in range(len(pts) - 1) if pts[i][0] <= km <= pts[i + 1][0])
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        base = y0 + (y1 - y0) * (km - x0) / (x1 - x0)
        year = data.get("year") or self.ref_year
        base *= 1 + max(-0.15, min(0.15, self.year_pct / 100.0 * (year - self.ref_year)))
        base -= self.paint_part_tl * len(data.get("paint_parts") or [])
        city = fold(data.get("city") or "")
        if city in EAST:
            base -= max(self.east_tl, self.nonmetro_tl)
        elif city and city not in METRO:
            base -= self.nonmetro_tl
        title = fold(data.get("title") or "")
        for words, tl in self.premiums:
            if any(w in title for w in words):
                base += tl
        return base


@dataclass(frozen=True)
class Rule:
    brand: str
    model: str = ""
    year_min: int | None = None
    year_max: int | None = None
    km_max: int | None = None
    max_price: float | None = None
    search_url: str | None = None
    km_min: int | None = None
    engine: str = ""                 # örn. "1.6 TDI" ya da seçenekli "1.6 TDI|1.4 TSI": seçeneklerden birinin kelimeleri başlıkta geçmeli
    trim: str = ""                   # örn. "Comfortline" ya da "Comfortline|Highline"
    km_per_year_max: int | None = None   # örn. 20000: km <= 20000 x araç yaşı (en az 1 yıl)
    forbidden_parts: tuple[str, ...] = ()  # boya ya da değişen parça adlarında bu sözcük geçerse elenir (örn. "tavan")
    gearbox: str = ""                # örn. "DSG"
    max_degisen: int | None = None   # parça parça ekspertiz; bölüm ilanda yoksa kural karşılanamaz
    max_boyali: int | None = None    # lokal boyalı + boyalı toplamı
    allowed_paint_parts: tuple[str, ...] = ()   # boya yalnız bu sözcükleri içeren parçalarda olabilir (örn. "camurluk")
    max_tramer_tl: int | None = None
    baseline: float | None = None    # galericinin piyasa ortalaması (yalnız mesajda kıyas için)
    market_min_profit_tl: int | None = None  # emsal kıyasında bu kurala özel TL tabanı (yoksa galerici düzeyindeki değer)
    price_model: PriceModel | None = None    # varsa bu kurala uyan ilanda fiyat kararı galericinin bantlarıyla verilir
    exclude_words: tuple[str, ...] = ()      # başlıkta geçerse kural uymaz (örn. sedan kuralında "variant")

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
        if self.exclude_words and any(w in fold(title) for w in self.exclude_words):
            return False
        year = data.get("year") or 0
        if self.year_min and year < self.year_min:
            return False
        if self.year_max and year > self.year_max:
            return False
        km = data.get("km") or 0
        if self.km_max and km and km > self.km_max:
            return False
        if self.km_min and km < self.km_min:
            return False
        head = tokens(f"{data.get('trim', '')} {title}")
        for need in (self.engine, self.trim):
            if need and not any(tokens(alt) <= head for alt in need.split("|")):
                return False
        if self.km_per_year_max and km and data.get("year"):
            if km > self.km_per_year_max * max(1, datetime.now().year - data["year"]):
                return False
        if self.gearbox:
            want = fold(self.gearbox)
            have = fold(f"{data.get('gearbox') or ''} {data.get('vites') or ''} {title}")
            # arabam'da DSG "Yarı Otomatik" vites tipiyle listelenir; şanzıman satırı eksik ilanlarda buna bakılır
            if want not in have and not (want == "dsg" and "yari otomatik" in have):
                return False
        return self._expertise_ok(data)

    def _expertise_ok(self, data: dict) -> bool:
        if self.max_degisen is not None:
            changed = data.get("changed_parts")
            if changed is None or len(changed) > self.max_degisen:
                return False
        wants_paint = self.max_boyali is not None or self.allowed_paint_parts or self.forbidden_parts
        if wants_paint:
            paint = data.get("paint_parts")
            if paint is None:
                return False
            if self.max_boyali is not None and len(paint) > self.max_boyali:
                return False
            if self.allowed_paint_parts and not all(
                    any(w in fold(p) for w in self.allowed_paint_parts) for p in paint):
                return False
            if self.forbidden_parts and any(
                    w in fold(p) for p in paint + (data.get("changed_parts") or []) for w in self.forbidden_parts):
                return False
        if self.max_tramer_tl is not None:
            tramer = data.get("tramer_tl")   # None = ilanda belirtilmemiş: elenmez, mesajda uyarı çıkar (satıcıya sorulur)
            if tramer is not None and tramer > self.max_tramer_tl:
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
    strong_tl: int = 150_000             # bu TL ve üstü fark = "güçlü fırsat" (günlük özette sayılır)
    market_min_profit_tl: int = 0        # >0: emsal ortancasından en az bu kadar TL ucuz olmalı (yüzde alt sınır yine geçerli)
    allow_damaged: bool = False
    active: bool = True
    urgent_words: tuple[str, ...] = ()   # başlıkta geçerse fiyat şartı aranmaz, kurala uyan ilan direkt gider (acil satılık, kelepir...)
    blacklist: tuple[str, ...] = ()   # ilan başlığı/açıklamasında geçerse elenir ("yok" ile olumsuzlananlar sayılmaz)


_NEGATIONS = ("yok", "degil", "bulunmamakta", "bulunmuyor", "olmayan", "sifir")


def blacklisted(text: str, words: tuple[str, ...]) -> str | None:
    """Kara liste kelimesi geçiyorsa kelimeyi döner. 'Pert kaydı yoktur' gibi olumsuzlanmış geçişler sayılmaz."""
    f = fold(text)
    for w in words:
        key = fold(w)
        pos = f.find(key)
        while pos != -1:
            tail = f[pos + len(key): pos + len(key) + 30]
            if not any(n in tail for n in _NEGATIONS):
                return w
            pos = f.find(key, pos + 1)
    return None


def urgent_hit(title: str, words: tuple[str, ...]) -> str | None:
    """Başlıkta (açıklamada değil: arabam şablon açıklamalarında 'fırsat' geçer) tam sözcük olarak geçen acil/kelepir ifadesi."""
    f = fold(title)
    for w in words:
        if re.search(r"(?<![a-z0-9])" + re.escape(fold(w)) + r"(?![a-z0-9])", f):
            return w
    return None


def _num(value, field_name: str, ctx: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise DealerConfigError(f"{ctx}: '{field_name}' sayı olmalı (gelen: {value!r})")


def _price_model(raw: dict | None) -> PriceModel | None:
    if not raw:
        return None
    bands = tuple(sorted((int(b[0]), int(b[1]), int(b[2]), int(b[3])) for b in raw["bands"]))
    return PriceModel(
        ref_year=int(raw["ref_year"]), bands=bands, year_pct=float(raw.get("year_pct", 3.5)),
        paint_part_tl=int(raw.get("paint_part_tl", 0)), east_tl=int(raw.get("east_tl", 0)),
        nonmetro_tl=int(raw.get("nonmetro_tl", 0)),
        premiums=tuple((tuple(fold(w) for w in p["words"]), int(p["tl"])) for p in raw.get("premiums", [])))


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
            baseline = _num(r.get("baseline"), "baseline", rctx)
            if max_price is None and baseline is not None:
                # hedef = baz fiyattan en az X TL aşağısı (min_profit_tl) ya da yüzde X altı (min_discount_pct); ikisi de yoksa baz fiyatın kendisi
                profit = _num(r.get("min_profit_tl"), "min_profit_tl", rctx)
                max_price = baseline - profit if profit else                     baseline * (1 - (_num(r.get("min_discount_pct"), "min_discount_pct", rctx) or 0.0) / 100.0)
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
                km_min=int(r["km_min"]) if r.get("km_min") else None,
                engine=str(r.get("engine") or "").strip(),
                trim=str(r.get("trim") or "").strip(),
                gearbox=str(r.get("gearbox") or "").strip(),
                max_degisen=int(r["max_degisen"]) if r.get("max_degisen") is not None else None,
                max_boyali=int(r["max_boyali"]) if r.get("max_boyali") is not None else None,
                allowed_paint_parts=tuple(fold(x) for x in (r.get("allowed_paint_parts") or [])),
                forbidden_parts=tuple(fold(x) for x in (r.get("forbidden_parts") or [])),
                km_per_year_max=int(r["km_per_year_max"]) if r.get("km_per_year_max") else None,
                max_tramer_tl=int(r["max_tramer_tl"]) if r.get("max_tramer_tl") is not None else None,
                baseline=baseline,
                market_min_profit_tl=int(r["market_min_profit_tl"]) if r.get("market_min_profit_tl") else None,
                price_model=_price_model(r.get("price_model")),
                exclude_words=tuple(fold(x) for x in (r.get("exclude_words") or [])),
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
            market_min_profit_tl=int(d.get("market_min_profit_tl", 0) or 0),
            strong_tl=int(d.get("strong_tl", 150_000)),
            allow_damaged=bool(d.get("allow_damaged", False)),
            active=bool(d.get("active", True)),
            blacklist=tuple(str(x) for x in (d.get("blacklist") or [])),
            urgent_words=tuple(str(x) for x in (d.get("urgent_words") or [])),
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


async def evaluate(data: dict, dealers: list[Dealer], fallback_chat_id: str,
                   bench=None) -> tuple[list[Candidate], BenchBudget | None]:
    """Her galerici için en fazla bir bildirim üretir: (bildirimler, ertelenen_hata).

    Piyasa kontrolü (market_check) arabam'daki aynı donanımdaki güncel ilanlarla yapılır (bench); emsal yoksa
    kontrol atlanır. Hedef fiyat ya da acil başlık zaten tetiklenmişse emsal sayfası hakkı dolsa bile bildirim
    üretilir. Emsal hakkı yüzünden değerlendirilemeyen galerici varsa hata ayrıca döner; çağıran, başarılı
    bildirimleri kuyruğa alıp ilanı sonraki döngüde yeniden değerlendirir (mükerrer bildirim alerts tablosundaki
    benzersizlik kısıtıyla engellenir).
    """
    price = float(data["price"])
    out: list[Candidate] = []
    deferred: BenchBudget | None = None
    for dealer in dealers:
        if data.get("is_heavy_damage") and not dealer.allow_damaged:
            continue
        matched = [r for r in dealer.rules if r.matches(data)]
        if not matched:
            continue
        hit_word = blacklisted(f"{data.get('title', '')} {data.get('description', '')}", dealer.blacklist)
        if hit_word:
            logger.info(f"Galerici {dealer.id}: kara liste kelimesi '{hit_word}' -> {data['url']}")
            continue

        target_hits = [r for r in matched if r.max_price is not None and price <= r.max_price]
        urgent = urgent_hit(data.get("title", ""), dealer.urgent_words)
        applicable = [(r, e) for r in matched if r.price_model and (e := r.price_model.expected(data)) is not None]
        avg = p25 = 0.0
        source = ""   # emsal değerinin kaynağı: karşılaştırma düzeyi
        estimated = False
        market_hit = False
        if dealer.market_check and bench is not None:
            try:
                # İnternet tahmini (ücretli, kesin değil) yalnız başka karar yolu yokken sorulur
                res = await bench.stats(data, allow_estimate=not (applicable or target_hits or urgent))
            except BenchBudget as exc:
                if not target_hits and not urgent:
                    deferred = deferred or exc
                    continue
                res = None
            if res is not None:
                avg, p25, estimated = res.median, res.p25, res.estimated
                floors = [r.market_min_profit_tl for r in matched if r.market_min_profit_tl is not None]
                floor = min(floors) if floors else dealer.market_min_profit_tl
                if estimated:
                    source = "İnternet taraması tahmini (kesin değil)"
                    market_hit = (bench.cfg.est_min_discount_pct <= discount_pct(price, avg) <= bench.cfg.est_max_discount_pct
                                  and avg - price >= floor)
                else:
                    source = f"{res.level} {res.comparables} güncel ilanın ortası"
                    market_hit = (discount_pct(price, avg) >= dealer.market_discount_pct and avg - price >= floor
                                  and (not p25 or price <= p25))

        model_note = ""
        gap_tl = avg - price if avg else 0.0
        if applicable:
            rule, expected = max(applicable, key=lambda t: t[1] - price)
            floor = rule.market_min_profit_tl or dealer.market_min_profit_tl
            gap_tl = expected - price
            market_hit = gap_tl >= floor and (not p25 or price <= p25)   # galerici bandı karar verir, emsal alt çeyreği veto eder
            model_note = f"📐 Fiyat modeli: referans {fmt_tl(expected)} TL → ilan {fmt_tl(abs(gap_tl))} TL {'altında' if gap_tl >= 0 else 'üstünde'}"
        if not target_hits and not market_hit and not urgent:
            continue

        lines = [f"🏪 {dealer.name} — uygun ilan", "", listing_block(data), "", f"💰 İlan: {fmt_tl(price)} TL"]
        reasons = []
        for r in target_hits[:2]:
            lines.append(f"🎯 Hedef: {r.label()} ≤ {fmt_tl(r.max_price)} TL → {fmt_tl(r.max_price - price)} TL altında")
            if r.baseline:
                lines.append(f"📐 Baz fiyat {fmt_tl(r.baseline)} TL → ilan %{fmt_pct(discount_pct(price, r.baseline))} altında")
            reasons.append(f"hedef {r.label()} ≤ {fmt_tl(r.max_price)}")
        if model_note:
            lines.append(model_note)
        if urgent:
            lines.append(f"🚨 Başlıkta \"{urgent}\" geçiyor: satıcı acil satmak istiyor, pazarlık payı olabilir.")
            reasons.append(f"acil: {urgent}")
        if avg > 0:
            pct = discount_pct(price, avg)
            lines.append(f"{'🔮' if estimated else '📊'} {source}: {fmt_tl(avg)} TL (ilan %{fmt_pct(pct)} / {fmt_tl(abs(avg - price))} TL {'altında' if pct >= 0 else 'üstünde'})")
            if market_hit:
                reasons.append(f"piyasa {'tahmini ' if estimated else ''}-%{fmt_pct(pct)}")   # "tahmin" oy sayımını ayırır
                if estimated:
                    lines.append("⚠️ Bu bir tahmindir, fiyat doğrulanamadı: ilanı iyi kontrol edin.")
        if any(r.max_tramer_tl is not None for r in matched) and data.get("tramer_tl") is None:
            lines.append("⚠️ Tramer tutarı ilanda belirtilmemiş: satıcıya sorun.")
        if data.get("is_heavy_damage"):
            lines.append("⚠️ Hasar/boya kaydı bulunan ilan: " + ", ".join(data.get("damage_reasons") or ["kontrol edin"]))
        if data.get("phone"):
            lines.append(f"📞 {data['phone']}")
        lines += ["", f"🔗 {data['url']}"]

        chat_id = dealer.chat_id or fallback_chat_id
        if not dealer.chat_id:
            logger.warning(f"Galerici {dealer.id} için chat_id yok; bildirim yönetici sohbetine gidiyor.")
        strong = "★ " if gap_tl >= dealer.strong_tl else ""
        out.append(Candidate("dealer", dealer.id, chat_id, strong + " + ".join(reasons), "\n".join(lines)))
    return out, deferred
