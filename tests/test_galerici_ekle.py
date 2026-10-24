import json
import unittest
from pathlib import Path

from galerici_ekle import birlestir, profil
from src.modes.dealer import parse_dealers

ORNEK = Path(__file__).resolve().parent.parent / "galerici_formu_ornek.json"


class GalericiEkleTests(unittest.TestCase):
    def test_example_form_becomes_valid_profile(self):
        p = profil(json.loads(ORNEK.read_text(encoding="utf-8")))
        d = parse_dealers({"dealers": [p]})[0]
        r = d.rules[0]
        self.assertEqual((d.market_min_profit_tl, d.market_discount_pct), (100000, 3))
        self.assertEqual(r.search_url, "https://www.arabam.com/ikinci-el/otomobil/volkswagen-passat?minYear=2015&maxYear=2017")
        self.assertEqual((r.engine, r.trim, r.exclude_words), ("1.6 TDI", "Comfortline|Highline", ("variant",)))
        self.assertEqual(r.price_model.bands[0], (110000, 125000, 1620000, 1750000))   # bin TL -> TL
        self.assertTrue(d.urgent_words and d.blacklist)

    def test_merge_replaces_same_id(self):
        form = json.loads(ORNEK.read_text(encoding="utf-8"))
        a = profil(form)
        cfg = birlestir(Path("yok-boyle-bir-dosya.json"), a)
        b = dict(a, name="Yeni Ad")
        cfg2 = {"dealers": [d for d in cfg["dealers"] if d["id"] != b["id"]] + [b]}
        self.assertEqual(len(parse_dealers(cfg2)), 1)


if __name__ == "__main__":
    unittest.main()
