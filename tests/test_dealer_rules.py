import unittest

from src.modes.dealer import blacklisted, parse_dealers
from src.parsing import parse_expertise, tramer_from_text

BODY = """Boya, Değişen ve Tramer
Orijinal
Boyalı
Lokal Boyalı
Değişmiş
Belirtilmemiş
Orjinal
Sağ Arka Çamurluk
Arka Kaput
Tavan
Motor Kaputu
Lokal boyalı
Sol Ön Çamurluk
Boyalı
-
Değişmiş
-
Belirtilmemiş
-
Tramer tutarı: 7.500 TL
Araç bilgileri
Şanzıman
DSG
"""

RULE = {"id": "d", "chat_id": "1", "rules": [{
    "brand": "Volkswagen", "model": "Passat", "year_min": 2016, "year_max": 2016, "km_min": 150000, "km_max": 180000,
    "engine": "1.6 TDI", "trim": "Comfortline", "gearbox": "DSG", "max_degisen": 0, "max_boyali": 2,
    "allowed_paint_parts": ["çamurluk", "kapı"], "max_tramer_tl": 10000, "baseline": 1620000, "min_discount_pct": 10}]}


def car(**kw):
    d = {"title": "Sahibinden Volkswagen Passat 1.6 TDi BlueMotion Comfortline 2016 Model İzmir 160.000 KM",
         "brand": "Volkswagen", "model": "Passat", "trim": "1.6 TDi BlueMotion Comfortline", "year": 2016,
         "km": 160000, "price": 1400000.0, "gearbox": "DSG", "paint_parts": ["Sol Ön Çamurluk"],
         "changed_parts": [], "tramer_tl": 5000}
    d.update(kw)
    return d


class ExpertiseTests(unittest.TestCase):
    def test_parse_parts_tramer_gearbox(self):
        e = parse_expertise(BODY)
        self.assertEqual(e["paint_parts"], ["Sol Ön Çamurluk"])
        self.assertEqual(e["changed_parts"], [])
        self.assertEqual(e["tramer_tl"], 7500)
        self.assertEqual(e["gearbox"], "DSG")

    def test_no_tramer_means_zero_and_missing_section_is_unknown(self):
        self.assertEqual(parse_expertise(BODY.replace("Tramer tutarı: 7.500 TL", "Tramer tutarı yok"))["tramer_tl"], 0)
        self.assertIsNone(parse_expertise("başka bir sayfa")["paint_parts"])


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.rule = parse_dealers([RULE])[0].rules[0]

    def test_target_price_from_baseline_and_discount(self):
        self.assertEqual(self.rule.max_price, 1620000 * 0.9)

    def test_matching_car(self):
        self.assertTrue(self.rule.matches(car()))

    def test_unknown_tramer_passes_and_semi_auto_counts_as_dsg(self):
        self.assertTrue(self.rule.matches(car(tramer_tl=None)))
        self.assertTrue(self.rule.matches(car(gearbox=None, vites="Yarı Otomatik")))
        self.assertFalse(self.rule.matches(car(gearbox=None, vites="Düz", title="Passat 1.6 TDi Comfortline 2016")))

    def test_each_requirement_rejects(self):
        for bad in (car(km=140000), car(km=190000), car(year=2017), car(gearbox="Manuel", title="Passat 1.6 TDi Comfortline 2016"),
                    car(trim="1.6 TDi BlueMotion Highline", title="Passat Highline 2016"),
                    car(changed_parts=["Sağ Ön Çamurluk"]), car(paint_parts=["Tavan"]),
                    car(paint_parts=["Sol Ön Çamurluk", "Sağ Ön Çamurluk", "Sol Ön Kapı"]),
                    car(paint_parts=["Motor Kaputu"]), car(tramer_tl=15000),
                    car(paint_parts=None)):
            self.assertFalse(self.rule.matches(bad), bad)


class TramerTextTests(unittest.TestCase):
    def test_description_tramer(self):
        self.assertEqual(tramer_from_text("TRAMER 2Bin TL, bakımları yapıldı"), 2000)
        self.assertEqual(tramer_from_text("Tramer kaydı YOKTUR."), 0)
        self.assertEqual(tramer_from_text("hatasız boyasız tramersiz"), 0)
        self.assertEqual(tramer_from_text("tramer 8.500 tl"), 8500)
        self.assertIsNone(tramer_from_text("temiz araç, tramer sorulur"))


class BlacklistTests(unittest.TestCase):
    WORDS = ("pert kaydı", "bedelsiz tramer", "tavan boyalı")

    def test_hit_and_negation(self):
        self.assertEqual(blacklisted("Araç pert kaydı var, acil", self.WORDS), "pert kaydı")
        self.assertIsNone(blacklisted("Pert kaydı yoktur, hatasız", self.WORDS))
        self.assertEqual(blacklisted("pert kaydı yok ama Tavan Boyalı", self.WORDS), "tavan boyalı")
        self.assertIsNone(blacklisted("temiz araç", self.WORDS))


if __name__ == "__main__":
    unittest.main()
