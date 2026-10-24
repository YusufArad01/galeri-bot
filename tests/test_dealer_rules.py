import unittest

from src.config import settings
from src.notifications import flush_alerts
from tests.test_pipeline import FakeScraper, PipelineBase, car as pipeline_car

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


class DealerHoldTests(PipelineBase):
    async def test_night_dealer_alert_waits_and_goes_when_hours_open(self):
        FakeScraper.pages[self.URL] = [pipeline_car(1, 700_000)]
        dealers = parse_dealers({"dealers": [{"id": "d", "chat_id": "42", "rules": [
            {"brand": "Renault", "model": "Megane", "max_price": 800_000}]}]})
        settings.DEALER_ACTIVE_START, settings.DEALER_ACTIVE_END = 0, 0     # kapalı
        async with self.Session() as s:
            st = await self.cycle(s, modes=("dealer",), dealers=dealers)
            self.assertEqual((st.alerts_queued, st.alerts_sent, len(self.sent)), (1, 0, 0))   # kuyrukta bekler
            settings.DEALER_ACTIVE_START, settings.DEALER_ACTIVE_END = 0, 24                   # açıldı
            sent, _ = await flush_alerts(s, self.telegram)
            self.assertEqual((sent, len(self.sent)), (1, 1))
            self.assertEqual(self.sent[0]["chat_id"], "42")


class DealerHoursTests(unittest.TestCase):
    def test_hours_are_turkey_time(self):
        from datetime import datetime
        from src.notifications import dealer_hours_open
        self.assertFalse(dealer_hours_open(datetime(2026, 10, 24, 1, 51)))    # 04:51 TR
        self.assertTrue(dealer_hours_open(datetime(2026, 10, 24, 5, 0)))      # 08:00 TR
        self.assertTrue(dealer_hours_open(datetime(2026, 10, 24, 19, 59)))    # 22:59 TR
        self.assertFalse(dealer_hours_open(datetime(2026, 10, 24, 20, 0)))    # 23:00 TR


class BlacklistTests(unittest.TestCase):
    WORDS = ("pert kaydı", "bedelsiz tramer", "tavan boyalı")

    def test_hit_and_negation(self):
        self.assertEqual(blacklisted("Araç pert kaydı var, acil", self.WORDS), "pert kaydı")
        self.assertIsNone(blacklisted("Pert kaydı yoktur, hatasız", self.WORDS))
        self.assertEqual(blacklisted("pert kaydı yok ama Tavan Boyalı", self.WORDS), "tavan boyalı")
        self.assertIsNone(blacklisted("temiz araç", self.WORDS))


if __name__ == "__main__":
    unittest.main()
