"""Saf (tarayıcısız) ayrıştırma yardımcıları: fiyat, km, özellik tablosu, hasar tespiti.

Scraper ham DOM verisini toplar, burası anlamlandırır. Böylece mantık canlı siteye
ihtiyaç duymadan test edilebilir.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_TR_MAP = str.maketrans({"ı": "i", "İ": "i"})

KNOWN_MULTIWORD_BRANDS = [
    "mercedes - benz", "mercedes-benz", "land rover", "alfa romeo", "aston martin",
    "rolls royce", "rolls-royce", "mini cooper",
]
_SELLER_PREFIX = re.compile(r"^(galeriden|sahibinden|yetkili bayiden)\s+", re.IGNORECASE)


def fold(text: str | None) -> str:
    """Küçük harfe çevirir, Türkçe karakterleri ve aksanları ASCII'ye indirger."""
    if not text:
        return ""
    text = text.translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def alnum_key(text: str | None) -> str:
    """'Mercedes - Benz' ve 'mercedes-benz' aynı anahtarı üretsin."""
    return re.sub(r"[^a-z0-9]", "", fold(text))


def tokens(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", fold(text)))


def parse_tl(text: str | None) -> float:
    """'905.000 TL' -> 905000.0 ; '1.250.000,50 ₺' -> 1250000.5"""
    if not text:
        raise ValueError("empty price")
    m = re.search(r"\d[\d.]*(?:,\d+)?", text)
    if not m:
        raise ValueError(f"no number in price text {text!r}")
    return float(m.group().replace(".", "").replace(",", "."))


def parse_int(text: str | None) -> int:
    """'203.000 KM' -> 203000 ; bulunamazsa 0."""
    if not text:
        return 0
    m = re.search(r"\d[\d.]*", text)
    return int(m.group().replace(".", "")) if m else 0


def source_id_from_url(url: str) -> str:
    """arabam.com ilan URL'sinin sonundaki ilan numarası."""
    m = re.search(r"/(\d+)/?(?:[?#].*)?$", url)
    if m:
        return m.group(1)
    return url.rstrip("/").split("/")[-1].split("-")[-1]


# --------------------------------------------------------------------------- #
# Boya / değişen / hasar
# --------------------------------------------------------------------------- #
@dataclass
class BoyaDegisen:
    degisen: int = 0
    boyali: int = 0  # lokal boyalı dahil
    tamami_boyali: bool = False


def parse_boya_degisen(value: str | None) -> BoyaDegisen:
    f = fold(value)
    out = BoyaDegisen()
    if not f:
        return out
    if re.search(r"(tamami|komple)\s+(boyali|degisen)", f):
        out.tamami_boyali = True
    for m in re.finditer(r"(\d+)\s+degisen", f):
        out.degisen += int(m.group(1))
    for m in re.finditer(r"(\d+)\s+(?:lokal\s+)?boyali", f):
        out.boyali += int(m.group(1))
    return out


_NEGATION_AFTER = ("yok", "degil", "bulunmam", "bulunmaz", "olmayan", "yoktur", "degildir", "mevcut degil")
_DAMAGE_WORD = re.compile(r"\b(agir\s+hasar\w*|pert\w*)")


def text_mentions_heavy_damage(text: str | None) -> bool:
    """Açıklama/başlıkta ağır hasar/pert geçiyor mu? 'ekspertiz', 'pert kaydı yok',
    'ağır hasarsız' gibi olumsuzlamaları sayma."""
    f = fold(text)
    for m in _DAMAGE_WORD.finditer(f):
        word = m.group(1)
        if word.endswith("siz") or word.endswith("sizdir"):
            continue
        tail = f[m.end(): m.end() + 40]
        # cümle sınırından sonrasına bakma
        tail = re.split(r"[.!?\n]", tail)[0]
        if any(neg in tail for neg in _NEGATION_AFTER):
            continue
        return True
    return False


@dataclass
class DamageReport:
    heavy: bool = False
    reasons: list[str] = field(default_factory=list)


def assess_damage(props: dict[str, str], title: str, description: str,
                  max_degisen: int = 2, max_boyali: int = 4) -> DamageReport:
    rep = DamageReport()
    folded_props = {fold(k): v for k, v in props.items()}

    for key, value in folded_props.items():
        fv = fold(value)
        if "agir hasar" in key or "agir hasar" in fv:
            if re.search(r"\b(yok|hayir)\b", fv):
                continue
            if re.search(r"\b(var|evet)\b", fv):
                rep.heavy = True
                rep.reasons.append("ağır hasar kaydı var")

    for key, value in folded_props.items():
        if "boya" in key or "degisen" in key:
            bd = parse_boya_degisen(value)
            if bd.tamami_boyali:
                rep.heavy = True
                rep.reasons.append("tamamı boyalı/değişen")
            elif bd.degisen > max_degisen:
                rep.heavy = True
                rep.reasons.append(f"{bd.degisen} değişen")
            elif bd.boyali > max_boyali:
                rep.heavy = True
                rep.reasons.append(f"{bd.boyali} boyalı")

    if text_mentions_heavy_damage(f"{title}. {description}"):
        rep.heavy = True
        rep.reasons.append("ilan metninde ağır hasar/pert")
    return rep


# --------------------------------------------------------------------------- #
# Özellik tablosu -> alanlar
# --------------------------------------------------------------------------- #
def _split_brand_from_title(title: str) -> tuple[str, str]:
    clean = _SELLER_PREFIX.sub("", title.strip())
    low = clean.lower()
    for b in KNOWN_MULTIWORD_BRANDS:
        if low.startswith(b):
            rest = clean[len(b):].strip().split(" ")
            return clean[: len(b)], rest[0] if rest and rest[0] else "Bilinmiyor"
    words = clean.split(" ")
    brand = words[0] if words and words[0] else "Bilinmiyor"
    model = words[1] if len(words) >= 2 else "Bilinmiyor"
    return brand, model


def parse_listing_fields(props: dict[str, str], title: str, breadcrumbs: list[str] | None = None,
                         location: str = "") -> dict:
    """Marka, seri (model), donanım (trim), yıl, km, şehir, satıcı tipi."""
    f = {fold(k): v for k, v in props.items()}

    year = 0
    m = re.search(r"\b(19|20)\d{2}\b", f.get("yil", ""))
    if m:
        year = int(m.group())
    if not year:
        m = re.search(r"\b((?:19|20)\d{2})\s+model", title, re.IGNORECASE)
        if m:
            year = int(m.group(1))

    km = parse_int(f.get("kilometre"))
    if not km:
        m = re.search(r"([\d.]+)\s*km", title, re.IGNORECASE)
        if m:
            km = parse_int(m.group(1))

    city = f.get("il") or f.get("sehir") or f.get("konum") or ""
    if not city and location:  # "Hacimbey Mh. Hendek, Sakarya" -> "Sakarya"
        city = location.split(",")[-1].strip()
    if not city:
        m = re.search(r"model\s+(.+?)\s+[\d.]+\s*km", title, re.IGNORECASE)
        if m:
            city = m.group(1).strip()
    city = city or "Bilinmiyor"

    brand = f.get("marka") or ""
    series = f.get("seri") or ""
    trim = f.get("model") or ""
    if re.fullmatch(r"\d{4}", trim.strip()):  # eski düzen: "Model" = yıl
        trim = ""

    if (not brand or not series) and breadcrumbs and len(breadcrumbs) >= 4:
        brand = brand or breadcrumbs[2]
        series = series or breadcrumbs[3]
    if not brand or not series:
        b, mdl = _split_brand_from_title(title)
        brand = brand or b
        series = series or mdl

    seller = f.get("kimden") or ""
    if not seller:
        low = title.lower()
        if low.startswith("sahibinden"):
            seller = "Sahibinden"
        elif low.startswith("galeriden") or low.startswith("yetkili"):
            seller = "Galeriden"
    return {
        "brand": brand or "Bilinmiyor",
        "model": series or "Bilinmiyor",
        "trim": trim,
        "year": year,
        "km": km,
        "city": city,
        "seller_type": seller or "Bilinmiyor",
    }


_PART_GROUPS = {"lokal boyali": "lokal", "boyali": "boyali", "degismis": "degisen", "belirtilmemis": "belirsiz"}


def parse_expertise(body: str | None) -> dict:
    """Detay sayfası metninden parça parça boya/değişen listesi, tramer tutarı ve şanzıman.
    Bölüm sayfada yoksa ilgili alanlar None kalır (bilinmiyor): kural koyan galerici için "doğrulanamadı" demektir."""
    out: dict = {"paint_parts": None, "changed_parts": None, "tramer_tl": None, "gearbox": None}
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    folded = [fold(ln) for ln in lines]
    for i, f in enumerate(folded):
        if f == "sanziman" and i + 1 < len(lines) and out["gearbox"] is None:
            out["gearbox"] = lines[i + 1]
    # alt başlık "Orjinal" (açıklama satırındaki "Orijinal" değil): ardından grup grup parça adları gelir
    start = next((i for i, f in enumerate(folded) if f == "orjinal"), None)
    if start is None:
        return out
    groups: dict[str, list[str]] = {"original": [], "lokal": [], "boyali": [], "degisen": [], "belirsiz": []}
    cur = "original"
    for ln, f in zip(lines[start + 1:], folded[start + 1:]):
        if f.startswith("tramer"):
            digits = re.sub(r"[^\d]", "", ln)
            out["tramer_tl"] = 0 if "yok" in f else (int(digits) if digits else None)
            break
        if f in _PART_GROUPS:
            cur = _PART_GROUPS[f]
        elif ln != "-":
            groups[cur].append(ln)
    out["paint_parts"] = groups["lokal"] + groups["boyali"]
    out["changed_parts"] = groups["degisen"]
    return out


def tramer_from_text(text: str | None) -> int | None:
    """Açıklamadan tramer tutarı: 'tramersiz', 'tramer kaydı yoktur' -> 0; 'tramer 2 bin TL', 'tramer 8.500' -> tutar.
    Belirsizse None (ilan sahibi yazmamış ya da anlaşılamadı)."""
    f = fold(text)
    if re.search(r"tramersiz|tramer\s+(?:kaydi\s+)?(?:yok|yoktur|sifir)|tramer\s+kaydi\s+bulunma", f):
        return 0
    m = re.search(r"tramer\W{0,3}(\d{1,3}(?:\.\d{3})+|\d+)(?:[.,]\d+)?\s*(bin|k\b)?", f)
    if not m:
        return None
    value = int(m.group(1).replace(".", ""))
    if m.group(2):
        value *= 1000
    return value if value >= 1000 else None


def _with_tramer_fallback(exp: dict, text: str) -> dict:
    """Yapısal 'Tramer tutarı belirtilmemiş' ise açıklamadaki tutara bakar."""
    if exp.get("tramer_tl") is None:
        exp["tramer_tl"] = tramer_from_text(text)
    return exp


_PHONE_RE = re.compile(r"(?<!\d)0[\s.]?(?:5\d{2}|[2-4]\d{2})[\s.]?\d{3}[\s.]?\d{2}[\s.]?\d{2}(?!\d)")


def phone_from_body(body: str | None) -> str:
    """Satıcı/ilan açıklaması bölümündeki ilk telefon numarası ("0 507 257 83 99" -> "0507 257 83 99"); yoksa boş.
    Yalnız 'Boya, Değişen ve Tramer' başlığından ÖNCEKİ metne bakılır: alt bilgideki arabam numaraları karışmasın."""
    head = (body or "").split("Boya, Değişen ve Tramer")[0][:8000]
    m = _PHONE_RE.search(head)
    if not m:
        return ""
    digits = re.sub(r"\D", "", m.group(0))
    return f"{digits[:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}"


def build_listing(url: str, title: str, price_text: str, description: str,
                  props: dict[str, str], breadcrumbs: list[str] | None = None,
                  max_degisen: int = 2, max_boyali: int = 4, crumbs: list[str] | None = None,
                  location: str = "", body: str = "") -> dict:
    """Ham DOM verisinden pipeline'ın beklediği ilan sözlüğünü üretir."""
    price = parse_tl(price_text)
    fields = parse_listing_fields(props, title, breadcrumbs, location)
    dmg = assess_damage(props, title, description, max_degisen, max_boyali)
    year, price_ = fields["year"], price
    # 2005 ve eski + 1 milyon TL altı = sıradan eski araç, arbitraj ilgisi yok
    old_cheap = 0 < year <= 2005 and price_ < 1_000_000.0
    own = BoyaDegisen()
    for k, v in props.items():
        if "boya" in fold(k) or "degisen" in fold(k):
            own = parse_boya_degisen(v)
    return {
        "degisen": own.degisen,
        "boyali": own.boyali,
        "source_listing_id": source_id_from_url(url),
        "title": title,
        "description": description or "",
        "url": url,
        "price": price,
        **fields,
        "is_heavy_damage": dmg.heavy,
        "damage_reasons": dmg.reasons,
        "is_old_cheap": old_cheap,
        "is_disqualified": dmg.heavy or old_cheap,
        "bench_urls": bench_urls_from_crumbs(crumbs or []),
        **_with_tramer_fallback(parse_expertise(body), f"{title} {description}"),
        "vites": props.get("vites tipi", ""),
        "phone": phone_from_body(body),
    }


# --------------------------------------------------------------------------- #
# Karşılaştırma sayfaları ve liste satırları
# --------------------------------------------------------------------------- #
_CRUMB_RE = re.compile(r"^/ikinci-el/otomobil/[a-z0-9-]+$")


def bench_urls_from_crumbs(crumbs: list[str]) -> list[str]:
    """Detay sayfasındaki yol çubuğu [marka, seri, motor, donanım] -> en özelinden genele
    karşılaştırma sayfaları (marka düzeyi çok geniş olduğu için atlanır)."""
    clean = [c.split("?")[0] for c in crumbs if _CRUMB_RE.match(c.split("?")[0])]
    clean = list(dict.fromkeys(clean))[:4]
    levels = clean[1:]
    return ["https://www.arabam.com" + c for c in reversed(levels)]


_DATE_RE = re.compile(r"^\d{1,2}\s+\w+\s+\d{4}$")


def parse_list_row(href: str, lines: list[str]) -> dict | None:
    """Liste sayfası satırı: [ad, başlık, yıl, km, renk, fiyat, tarih, il, ilçe]."""
    year = km = 0
    price = 0.0
    city = ""
    name = lines[0] if lines else ""
    for i, line in enumerate(lines):
        if re.fullmatch(r"(19|20)\d{2}", line):
            year = int(line)
        elif re.fullmatch(r"[\d.]+\s*KM", line, re.IGNORECASE):
            km = parse_int(line)
        elif re.fullmatch(r"[\d.]+\s*TL", line):
            price = parse_tl(line)
        elif _DATE_RE.match(line) and i + 1 < len(lines):
            city = lines[i + 1]
    if not (price and year):
        return None
    return {"id": source_id_from_url(href), "url": href, "name": name,
            "title": lines[1] if len(lines) > 1 else "", "year": year, "km": km, "price": price, "city": city}
