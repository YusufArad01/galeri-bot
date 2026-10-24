"""Galerici formunu (Türkçe alanlar) bot profiline çevirir ve dealers.json'a ekler.

    python galerici_ekle.py form.json            # dealers.json'a ekler/günceller, özet yazar
    python galerici_ekle.py form.json --railway  # ayrıca Railway'e yükler (canlıyı değiştirir, yeniden başlatır)

Form biçimi için galerici_formu_ornek.json ve GALERICI_EKLEME.md'ye bak.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.modes.dealer import parse_dealers
from src.parsing import fold

KARA_LISTE = ["ağır hasarlı", "pert kaydı", "bedelsiz tramer", "çekme belgeli", "şaseler işlemli", "podye boyalı",
              "airbag tamirli", "tavan boyalı", "fiyat semboliktir", "üste nakit alacağım", "vade olur",
              "takas fiyatı farklıdır"]
ACIL = ["acil", "acilen", "acilinden", "kelepir", "ihtiyaçtan", "borçtan", "zarardan", "değerinin altında",
        "piyasanın altında", "çok ucuz", "ucuza"]


def slug(text: str) -> str:
    return "-".join(fold(text).replace(".", " ").split())


def kural(k: dict, varsayilan_kar: int) -> dict:
    yil = k.get("yil") or [None, None]
    km = k.get("km_araligi") or [None, None]
    r = {"brand": k["marka"], "model": k.get("model", ""), "year_min": yil[0], "year_max": yil[1],
         "km_min": km[0], "km_max": km[1], "engine": k.get("motor", ""), "trim": k.get("donanim", ""),
         "gearbox": k.get("vites", ""), "max_degisen": k.get("degisen_en_fazla"), "max_boyali": k.get("boya_en_fazla"),
         "allowed_paint_parts": k.get("boya_yerleri", []), "forbidden_parts": k.get("yasak_parcalar", []),
         "max_tramer_tl": k.get("tramer_en_fazla"), "exclude_words": k.get("haric_kelimeler", []),
         "market_min_profit_tl": k.get("min_kar_tl", varsayilan_kar)}
    url = k.get("arama_adresi")
    if not url and yil[0] and yil[1]:
        url = f"https://www.arabam.com/ikinci-el/otomobil/{slug(k['marka'])}-{slug(k.get('model', ''))}".rstrip("-")
        url += f"?minYear={yil[0]}&maxYear={yil[1]}"
    if url:
        r["search_url"] = url
    fm = k.get("fiyat_modeli")
    if fm:   # bantlar bin TL / bin km olarak yazılır: [km_alt, km_üst, fiyat_alt, fiyat_üst]
        r["price_model"] = {
            "ref_year": fm["ref_yil"], "bands": [[b[0] * 1000, b[1] * 1000, b[2] * 1000, b[3] * 1000] for b in fm["bantlar"]],
            "year_pct": fm.get("yil_basina_yuzde", 3.5), "paint_part_tl": fm.get("boyali_parca_tl", 0),
            "east_tl": fm.get("dogu_dusus_tl", 0), "nonmetro_tl": fm.get("buyuksehir_disi_dusus_tl", 0),
            "premiums": [{"words": p["kelimeler"], "tl": p["tl"]} for p in fm.get("primler", [])]}
    return {key: v for key, v in r.items() if v not in (None, "", [])}


def profil(form: dict) -> dict:
    g = form["galerici"]
    v = form.get("varsayilan", {})
    kar = v.get("min_kar_tl", 100_000)
    return {"id": g["id"], "name": g.get("ad", g["id"]), "chat_id": str(g.get("telegram") or ""),
            "market_check": True, "market_discount_pct": v.get("alt_yuzde", 3), "market_min_profit_tl": kar,
            "allow_damaged": False,
            "blacklist": v.get("kara_liste", KARA_LISTE), "urgent_words": v.get("acil_kelimeler", ACIL),
            "rules": [kural(k, kar) for k in form["kurallar"]]}


def birlestir(dosya: Path, yeni: dict) -> dict:
    cfg = json.loads(dosya.read_text(encoding="utf-8")) if dosya.exists() else {"dealers": []}
    cfg["dealers"] = [d for d in cfg["dealers"] if d["id"] != yeni["id"]] + [yeni]
    parse_dealers(cfg)   # bozuk profili yazmadan yakala (DealerConfigError)
    return cfg


def railway_yukle(cfg: dict) -> None:
    import httpx
    from railway_auth import get_token
    q = ('mutation($v: String!) { variableUpsert(input:{projectId:"bb1d057a-1dc1-46e5-bf22-17db8ceaadfe", '
         'environmentId:"b97e3e4d-385c-4788-9272-b8ad9eebdd50", serviceId:"17fbe5e0-45e7-428a-8389-de570ba2f938", '
         'name:"DEALERS_JSON", value:$v}) }')
    r = httpx.post("https://backboard.railway.app/graphql/v2", timeout=60,
                   json={"query": q, "variables": {"v": json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))}},
                   headers={"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"})
    print("Railway:", r.json())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("form")
    ap.add_argument("--dosya", default="dealers.json")
    ap.add_argument("--railway", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    yeni = profil(json.loads(Path(a.form).read_text(encoding="utf-8")))
    cfg = birlestir(Path(a.dosya), yeni)
    Path(a.dosya).write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    urls = {r["search_url"] for d in cfg["dealers"] for r in d["rules"] if r.get("search_url")}
    print(f"{yeni['id']}: {len(yeni['rules'])} kural eklendi. Toplam {len(cfg['dealers'])} galerici, {len(urls)} farklı arama sayfası "
          f"(her tur ~{len(urls) * 2} liste isteği).")
    if not yeni["chat_id"]:
        print("UYARI: Telegram numarası yok; mesajlar yönetici sohbetine gider. Galerici bota /start yazınca numara sana bildirilir.")
    if a.railway:
        railway_yukle(cfg)


if __name__ == "__main__":
    main()
