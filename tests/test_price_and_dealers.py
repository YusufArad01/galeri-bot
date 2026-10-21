import json
import unittest

from src.modes.dealer import DealerConfigError, Rule, parse_dealers, search_urls
from src.pricing.perplexity import cache_key, parse_estimate


class PriceParseTests(unittest.TestCase):
    def test_json(self):
        self.assertEqual(parse_estimate('{"ortalama": 1250000, "min": 1100000, "max": 1400000}'), (1250000.0, 1100000.0, 1400000.0))

    def test_json_in_prose_with_turkish_numbers(self):
        txt = 'Tabii: {"ortalama": "1.250.000", "min": "1.100.000", "max": "1.400.000"} [1][2]'
        self.assertEqual(parse_estimate(txt)[0], 1250000.0)

    def test_json_without_average_uses_midpoint(self):
        self.assertEqual(parse_estimate('{"min": 1000000, "max": 1200000}')[0], 1100000.0)

    def test_plain_text_fallback_ignores_years_and_citations(self):
        avg, low, high = parse_estimate("2018 model araçlar ortalama 850.000 TL [1], aralık 800.000 - 900.000 TL")
        self.assertEqual((avg, low, high), (850000.0, 800000.0, 900000.0))

    def test_english_thousands(self):
        self.assertEqual(parse_estimate("about 850,000 TL")[0], 850000.0)

    def test_garbage(self):
        self.assertIsNone(parse_estimate("Bilgi bulunamadı"))
        self.assertIsNone(parse_estimate(""))
        self.assertIsNone(parse_estimate('{"ortalama": 12}'))  # mantıksız (50.000 TL altı)

    def test_cache_key_buckets_km(self):
        a = cache_key("Mercedes - Benz", "C 200", "AMG", 2018, 101_000)
        b = cache_key("mercedes-benz", "c 200", "amg", 2018, 120_000)
        c = cache_key("mercedes-benz", "c 200", "amg", 2018, 130_000)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


MC200 = {"brand": "Mercedes - Benz", "model": "C", "trim": "200 AMG", "year": 2018, "km": 120000,
         "title": "Sahibinden Mercedes - Benz C 200 AMG 2018 Model İstanbul 120.000 KM"}


class RuleTests(unittest.TestCase):
    def test_brand_and_model_matching(self):
        self.assertTrue(Rule("Mercedes-Benz", "C 200").matches(MC200))
        self.assertTrue(Rule("mercedes benz").matches(MC200))
        self.assertFalse(Rule("Mercedes-Benz", "E 200").matches(MC200))
        self.assertFalse(Rule("BMW", "C 200").matches(MC200))
        self.assertFalse(Rule("Mercedes-Benz", "C 20").matches(MC200))  # parça eşleşmesi yok

    def test_year_km_limits(self):
        self.assertTrue(Rule("Mercedes-Benz", "C 200", year_min=2016, year_max=2019, km_max=150000).matches(MC200))
        self.assertFalse(Rule("Mercedes-Benz", "C 200", year_min=2019).matches(MC200))
        self.assertFalse(Rule("Mercedes-Benz", "C 200", year_max=2017).matches(MC200))
        self.assertFalse(Rule("Mercedes-Benz", "C 200", km_max=100000).matches(MC200))

    def test_decimal_engine_tokens(self):
        d = {"brand": "Renault", "model": "Megane", "trim": "1.5 dCi Touch", "year": 2018, "km": 1, "title": ""}
        self.assertTrue(Rule("Renault", "Megane 1.5").matches(d))
        self.assertFalse(Rule("Renault", "Megane 1.6").matches(d))


class ProfileParseTests(unittest.TestCase):
    def good(self):
        return {"dealers": [{"id": "ahmet", "name": "Ahmet Oto", "chat_id": "123", "market_check": True,
                             "rules": [{"brand": "Mercedes-Benz", "model": "C 200", "max_price": 1450000,
                                        "search_url": "https://www.arabam.com/ikinci-el/otomobil/mercedes-benz-c-serisi"},
                                       {"brand": "BMW"}]}]}

    def test_valid(self):
        ds = parse_dealers(self.good())
        self.assertEqual(ds[0].rules[0].max_price, 1450000.0)
        self.assertIsNone(ds[0].rules[1].max_price)  # market_check açık olduğu için geçerli
        self.assertEqual(search_urls(ds), ["https://www.arabam.com/ikinci-el/otomobil/mercedes-benz-c-serisi"])

    def test_rule_without_price_needs_market_check(self):
        raw = self.good()
        raw["dealers"][0]["market_check"] = False
        with self.assertRaises(DealerConfigError):
            parse_dealers(raw)

    def test_validation_errors(self):
        for mutate in (lambda r: r["dealers"][0].pop("id"),
                       lambda r: r["dealers"][0].update(rules=[]),
                       lambda r: r["dealers"][0]["rules"][0].pop("brand"),
                       lambda r: r["dealers"][0]["rules"][0].update(max_price="abc"),
                       lambda r: r["dealers"].append(dict(r["dealers"][0]))):
            raw = json.loads(json.dumps(self.good()))
            mutate(raw)
            with self.assertRaises(DealerConfigError):
                parse_dealers(raw)


if __name__ == "__main__":
    unittest.main()
