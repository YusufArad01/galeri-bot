import unittest

from src.parsing import (assess_damage, build_listing, fold, parse_boya_degisen, parse_tl,
                         source_id_from_url, text_mentions_heavy_damage)

# Gerçek arabam.com ilanından (21.10.2026) alınan özellik tablosu
MEGANE_PROPS = {
    "ilan no": "44988930", "ilan tarihi": "21 Ekim 2026", "marka": "Renault", "seri": "Megane",
    "model": "1.5 dCi Touch", "yıl": "2018", "kilometre": "203.000 KM", "vites tipi": "Otomatik",
    "yakıt tipi": "Dizel", "ağır hasar kaydı": "Ağır hasar kaydı yok", "boya-değişen": "4 lokal boyalı",
    "kimden": "Galeriden",
}
MEGANE_TITLE = "Galeriden Renault Megane 1.5 dCi Touch 2018 Model Denizli 203.000 KM Beyaz"
MEGANE_URL = ("https://www.arabam.com/ilan/galeriden-satilik-renault-megane-1-5-dci-touch/"
              "2018-model-megane-touch-1-5-dizel-otomatik-firsat-araci-sonfiyat/44988930")


class ParsingTests(unittest.TestCase):
    def test_price_and_id(self):
        self.assertEqual(parse_tl("905.000 TL"), 905000.0)
        self.assertEqual(parse_tl("1.250.000,50 ₺"), 1250000.5)
        self.assertEqual(source_id_from_url(MEGANE_URL), "44988930")
        self.assertEqual(source_id_from_url(MEGANE_URL + "/"), "44988930")

    def test_real_listing_fields(self):
        d = build_listing(MEGANE_URL, MEGANE_TITLE, "905.000 TL", "Tedarik araçları şimdi yayında!", MEGANE_PROPS)
        self.assertEqual((d["brand"], d["model"], d["trim"]), ("Renault", "Megane", "1.5 dCi Touch"))
        self.assertEqual((d["year"], d["km"], d["price"]), (2018, 203000, 905000.0))
        # Eski kodda şehir alanına "203.000 KM" yazılıyordu ("kilometre" içinde "il" geçtiği için)
        self.assertEqual(d["city"], "Denizli")
        self.assertEqual(d["seller_type"], "Galeriden")
        self.assertFalse(d["is_heavy_damage"])  # 4 lokal boya: izin verilen en fazla 4

    def test_city_from_location_when_title_has_none(self):
        # Satıcı başlığa kendi cümlesini yazınca şehir başlıkta olmaz; konum alanından alınır
        d = build_listing(MEGANE_URL, "Ağır bakımları yeni yapıldı, triger, balata, yağ değişti", "875.000 TL", "",
                          {"marka": "Volkswagen", "seri": "Polo", "yıl": "2015", "kilometre": "121.000 KM"},
                          location="Hacimbey Mh. Hendek, Sakarya")
        self.assertEqual(d["city"], "Sakarya")

    def test_fields_fall_back_to_title(self):
        d = build_listing(MEGANE_URL, MEGANE_TITLE, "905.000 TL", "", {})
        self.assertEqual((d["year"], d["km"], d["city"]), (2018, 203000, "Denizli"))
        self.assertEqual(d["brand"], "Renault")

    def test_tamami_orjinal_is_not_damage(self):
        # Eski kod "tamamı" kelimesini görünce en temiz araçları pert sayıp eliyordu
        rep = assess_damage({"boya-değişen": "Tamamı orjinal"}, "x", "")
        self.assertFalse(rep.heavy)

    def test_boya_degisen_counts(self):
        bd = parse_boya_degisen("2 değişen, 3 lokal boyalı, 1 boyalı")
        self.assertEqual((bd.degisen, bd.boyali), (2, 4))
        self.assertTrue(parse_boya_degisen("Tamamı boyalı").tamami_boyali)
        self.assertTrue(assess_damage({"boya-değişen": "5 boyalı"}, "x", "").heavy)
        self.assertTrue(assess_damage({"boya-değişen": "3 değişen"}, "x", "").heavy)
        self.assertFalse(assess_damage({"boya-değişen": "2 değişen, 1 boyalı"}, "x", "").heavy)

    def test_heavy_damage_property(self):
        self.assertFalse(assess_damage({"ağır hasar kaydı": "Ağır hasar kaydı yok"}, "x", "").heavy)
        self.assertTrue(assess_damage({"ağır hasar kaydı": "Ağır hasar kaydı var"}, "x", "").heavy)

    def test_text_keywords(self):
        # "ekspertiz" içinde "pert" geçer; eski alt-dize kontrolü bu ilanları yanlışlıkla eliyordu
        self.assertFalse(text_mentions_heavy_damage("Ekspertiz raporu mevcuttur, değişensiz"))
        self.assertFalse(text_mentions_heavy_damage("Pert kaydı yoktur."))
        self.assertFalse(text_mentions_heavy_damage("Ağır hasar kaydı yok, hasarsız"))
        self.assertFalse(text_mentions_heavy_damage("ağır hasarsız temiz araç"))
        self.assertFalse(text_mentions_heavy_damage("Ağır hasarlı değildir"))
        self.assertTrue(text_mentions_heavy_damage("Pert kayıtlı araç, parça olarak satılık"))
        self.assertTrue(text_mentions_heavy_damage("AĞIR HASARLI araçtır"))
        self.assertTrue(text_mentions_heavy_damage("Pertli, şase hasarlı"))

    def test_old_cheap_flag(self):
        d = build_listing(MEGANE_URL, "Sahibinden Ford Focus 2003 Model Ankara 300.000 KM", "250.000 TL", "",
                          {"yıl": "2003"})
        self.assertTrue(d["is_old_cheap"] and d["is_disqualified"])

    def test_fold(self):
        self.assertEqual(fold("İSTANBUL Ağır Hasar ı"), "istanbul agir hasar i")


if __name__ == "__main__":
    unittest.main()
