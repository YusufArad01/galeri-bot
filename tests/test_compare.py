from datetime import datetime, timedelta

from sqlalchemy import select

from src.benchmark import BenchConfig
from src.config import Settings
from src.health import CycleStats, Health
from src.models.domain import Listing
from src.modes.compare import CompareConfig, release
from src.notifications import flush_alerts
from src.parsing import bench_urls_from_crumbs, parse_list_row
from src.modes.dealer import parse_dealers
from tests.test_pipeline import DEALERS, FakeScraper, PipelineBase, car

BASE = "https://www.arabam.com/ikinci-el/otomobil/renault-megane"
TRIM = BASE + "-1-5-dci-touch"
ENGINE = BASE + "-1-5-dci"
CRUMBS = ["/ikinci-el/otomobil/renault", "/ikinci-el/otomobil/renault-megane",
          "/ikinci-el/otomobil/renault-megane-1-5-dci", "/ikinci-el/otomobil/renault-megane-1-5-dci-touch"]


def row(i, price, year=2018, km=100000):
    return {"id": f"r{i}", "url": f"https://www.arabam.com/ilan/x/r{i}", "name": "Renault Megane 1.5 dCi Touch", "title": "t",
            "year": year, "km": km, "price": float(price), "city": "Izmir"}


def listing(i, price, **kw):
    d = car(i, price, **kw)
    d["bench_urls"] = bench_urls_from_crumbs(CRUMBS)
    return d


class ParseTests(PipelineBase):
    def test_bench_urls_most_specific_first(self):
        self.assertEqual(bench_urls_from_crumbs(CRUMBS), [TRIM, ENGINE, BASE])
        self.assertEqual(bench_urls_from_crumbs(CRUMBS[:2]), [BASE])            # sadece marka+seri
        self.assertEqual(bench_urls_from_crumbs(["/ikinci-el/otomobil"]), [])   # kırıntı yok

    def test_list_row(self):
        lines = ["Renault Megane 1.5 dCi Expression", "EMSALSİZ TEMİZLİKTE", "2009", "280.000 KM", "Gri",
                 "535.000 TL", "22 Ekim 2026", "Konya", "Selçuklu"]
        r = parse_list_row("https://www.arabam.com/ilan/a/b/44989139", lines)
        self.assertEqual((r["id"], r["year"], r["km"], r["price"], r["city"]),
                         ("44989139", 2009, 280000, 535000.0, "Konya"))
        self.assertIsNone(parse_list_row("https://x/ilan/1", ["sadece", "başlık"]))

    def test_modes_parse(self):
        self.assertEqual(Settings(BOT_MODE="compare").modes, {"compare"})
        self.assertEqual(Settings(BOT_MODE="all").modes, {"market", "dealer", "compare"})
        self.assertEqual(Settings(BOT_MODE="market, compare").modes, {"market", "compare"})
        with self.assertRaises(ValueError):
            Settings(BOT_MODE="digest").modes


class CompareModeTests(PipelineBase):
    CFG = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=1, release_minutes=0)

    def lists(self):
        # donanım sayfasında yeterli ilan yok (3), motor sayfasında 6 benzer ilan var
        FakeScraper.lists[TRIM] = [row(1, 1_000_000), row(2, 1_000_000), row(3, 1_000_000)]
        FakeScraper.lists[ENGINE] = [row(i, 1_000_000 + i * 1000) for i in range(10, 16)]
        FakeScraper.lists[BASE] = [row(i, 1_300_000) for i in range(20, 30)]

    async def test_sends_only_best_one_and_no_perplexity(self):
        self.lists()
        FakeScraper.pages[self.URL] = [
            listing(1, 850_000),   # %15 ucuz
            listing(2, 800_000),   # %20 ucuz -> turun en iyisi
            listing(3, 990_000),
            listing(4, 300_000, is_heavy_damage=True, is_disqualified=True),  # hasarlı: karşılaştırılmaz
        ]
        async with self.Session() as s:
            st = await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
            self.assertEqual(self.pplx_calls, 0)
            self.assertEqual(len(self.sent), 1)                        # her turda tek mesaj
            text = self.sent[0]["text"]
            self.assertIn("800.000 TL", text)
            self.assertIn("Aynı motordaki 6 güncel ilanın", text)      # donanım yetmedi, motor düzeyine düştü
            self.assertIn("(Son 3 saatte 2 aday arasından en iyisi)", text)
            self.assertNotIn("300.000", text)
            self.assertEqual(st.bench_pages, 2)                        # donanım + motor; seri sayfası açılmadı
            self.assertEqual(len((await s.execute(select(Listing))).scalars().all()), 4)

    async def test_pages_are_cached_between_cycles(self):
        self.lists()
        FakeScraper.pages[self.URL] = [listing(1, 800_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
            first = len(FakeScraper.list_fetches)
            FakeScraper.pages[self.URL] = [listing(2, 790_000)]
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual(first, 2)
        self.assertEqual(len(FakeScraper.list_fetches), 2)             # ikinci turda yeni sayfa açılmadı (önbellek)
        self.assertEqual(len(self.sent), 2)

    async def test_nothing_sent_when_not_cheap_enough(self):
        self.lists()
        FakeScraper.pages[self.URL] = [listing(1, 980_000), listing(2, 1_050_000)]
        async with self.Session() as s:
            st = await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual((self.sent, st.alerts_queued), ([], 0))

    async def test_page_budget_defers_listing_instead_of_losing_it(self):
        self.lists()
        FakeScraper.pages[self.URL] = [listing(1, 800_000)]
        async with self.Session() as s:
            st = await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig(max_fetch_per_cycle=1))
            self.assertEqual(st.deferred, 1)
            self.assertEqual(len((await s.execute(select(Listing))).scalars().all()), 0)  # kaydedilmedi: tekrar denenecek
            st2 = await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
            self.assertEqual((st2.deferred, st2.alerts_sent), (0, 1))

    async def test_empty_pages_do_not_crash_or_send(self):
        FakeScraper.pages[self.URL] = [listing(1, 800_000)]  # hiç liste verisi yok (engel gibi)
        async with self.Session() as s:
            st = await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual((self.sent, st.bench_empty), ([], 2))

    async def test_market_and_compare_together(self):
        self.lists()
        FakeScraper.pages[self.URL] = [listing(1, 800_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("market", "compare"), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual(sorted("🏆" in m["text"] for m in self.sent), [False, True])


class BlockNoteTests(PipelineBase):
    async def test_block_alert_contains_page_hint(self):
        async with self.Session() as s:
            h = Health(s, self.telegram, "ADMIN", blocked_alert_after=2)
            note = "başlık='Access Denied'; metin='Erişim engellendi'"
            await h.record_cycle(CycleStats(blocked=True, block_note=note))
            await h.record_cycle(CycleStats(blocked=True, block_note=note))
        self.assertIn("Sayfadan gelen ipucu", self.sent[0]["text"])
        self.assertIn("Access Denied", self.sent[0]["text"])


class TargetTests(PipelineBase):
    def test_pages_expand_arabam_only(self):
        from src.pipeline import build_targets
        t = build_targets(["https://www.arabam.com/ikinci-el/otomobil", "https://tr.vava.cars/buy/cars"],
                          [], {"market"}, pages=3)
        self.assertEqual(t, [
            "https://www.arabam.com/ikinci-el/otomobil?sort=DateDesc",
            "https://www.arabam.com/ikinci-el/otomobil?sort=DateDesc&page=2",
            "https://www.arabam.com/ikinci-el/otomobil?sort=DateDesc&page=3",
            "https://tr.vava.cars/buy/cars"])
        self.assertEqual(len(build_targets(["https://www.arabam.com/x"], [], {"market"})), 1)

    def test_dealer_pages_come_first_and_go_deeper(self):
        from src.pipeline import build_targets
        d = parse_dealers({"dealers": [{"id": "d", "rules": [{"brand": "VW", "max_price": 1, "search_url": "https://www.arabam.com/ikinci-el/otomobil/vw-passat"}]}]})
        t = build_targets(["https://www.arabam.com/ikinci-el/otomobil"], d, {"compare", "dealer"}, pages=2, dealer_pages=4)
        self.assertEqual(len(t), 4 + 2)
        self.assertTrue(t[0].startswith("https://www.arabam.com/ikinci-el/otomobil/vw-passat"))
        self.assertTrue(t[-1].endswith("page=2"))


class ReleaseTests(PipelineBase):
    def lists(self):
        FakeScraper.lists[TRIM] = [row(i, 1_000_000) for i in range(1, 7)]

    async def test_spacing_keeps_candidates_until_next_slot(self):
        self.lists()
        cfg = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=1, release_minutes=60)
        FakeScraper.pages[self.URL] = [listing(1, 850_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=cfg, bench_cfg=BenchConfig())
            self.assertEqual(len(self.sent), 1)                   # ilk gönderim hemen

            FakeScraper.pages[self.URL] = [listing(2, 800_000), listing(3, 820_000)]
            await self.cycle(s, modes=("compare",), compare=cfg, bench_cfg=BenchConfig())
            self.assertEqual(len(self.sent), 1)                   # 60 dk dolmadı: aday bekliyor

            queued = await release(s, cfg, "ADMIN", now=datetime.utcnow() + timedelta(minutes=61))
            await flush_alerts(s, self.telegram)
            self.assertEqual((queued, len(self.sent)), (1, 2))
            self.assertIn("800.000 TL", self.sent[1]["text"])     # bekleyenlerin en iyisi
            self.assertIn("(Son 3 saatte 2 aday arasından en iyisi)", self.sent[1]["text"])

    async def test_daily_cap(self):
        self.lists()
        cfg = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=1, release_minutes=0, daily_max=2)
        async with self.Session() as s:
            for i in (1, 2, 3):
                FakeScraper.pages[self.URL] = [listing(i, 800_000 + i)]
                await self.cycle(s, modes=("compare",), compare=cfg, bench_cfg=BenchConfig())
        self.assertEqual(len(self.sent), 2)

    async def test_absurdly_cheap_ranks_below_plausible_deal(self):
        self.lists()
        cfg = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=1, release_minutes=60)
        FakeScraper.pages[self.URL] = [listing(1, 450_000), listing(2, 800_000)]  # %55 şüpheli, %20 makul
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=cfg, bench_cfg=BenchConfig())
        self.assertEqual(len(self.sent), 1)
        self.assertIn("800.000 TL", self.sent[0]["text"])

    async def test_stale_candidates_expire(self):
        self.lists()
        cfg = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=1, release_minutes=60)
        FakeScraper.pages[self.URL] = [listing(1, 850_000), listing(2, 800_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=cfg, bench_cfg=BenchConfig())
            self.assertEqual(len(self.sent), 1)
            queued = await release(s, cfg, "ADMIN", now=datetime.utcnow() + timedelta(hours=4))
        self.assertEqual(queued, 0)  # bekleyen aday 3 saati geçti, gönderilmez


class ScheduleTests(PipelineBase):
    def lists(self):
        FakeScraper.lists[TRIM] = [row(i, 1_000_000) for i in range(1, 7)]

    async def test_best_two_of_the_window_per_slot(self):
        self.lists()
        cfg = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=2, release_minutes=120)
        FakeScraper.pages[self.URL] = [listing(1, 880_000), listing(2, 800_000), listing(3, 840_000), listing(4, 860_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=cfg, bench_cfg=BenchConfig())
        self.assertEqual(len(self.sent), 2)                         # 4 aday, en iyi 2'si
        prices = " ".join(m["text"] for m in self.sent)
        self.assertIn("800.000 TL", prices)
        self.assertIn("840.000 TL", prices)
        self.assertNotIn("880.000 TL", prices)

    async def test_no_messages_outside_active_hours(self):
        self.lists()
        cfg = CompareConfig(active_start=8, active_end=23, min_discount_pct=10.0, max_items=1, release_minutes=0,
                            fresh_hours=1000)
        FakeScraper.pages[self.URL] = [listing(1, 800_000)]
        night_tr = datetime(2026, 10, 23, 0, 30) - timedelta(hours=3)   # Türkiye saatiyle 00:30
        day_tr = datetime(2026, 10, 23, 9, 0) - timedelta(hours=3)      # Türkiye saatiyle 09:00
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=CompareConfig(
                active_start=0, active_end=24, min_discount_pct=10.0, max_items=0, release_minutes=0),
                bench_cfg=BenchConfig())                               # aday havuza girer, hiçbiri gönderilmez
            self.assertEqual(await release(s, cfg, "ADMIN", now=night_tr), 0)   # gece: gönderilmez
            self.assertEqual(await release(s, cfg, "ADMIN", now=day_tr), 1)     # gündüz: gider


class KmAdjustTests(PipelineBase):
    def lists(self):
        FakeScraper.lists[TRIM] = [row(i, 1_000_000, km=100_000) for i in range(1, 7)]

    async def test_high_km_car_is_not_a_false_deal(self):
        self.lists()
        cfg = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=1, release_minutes=0)
        # 200 bin km, 800 bin TL: benzerleri (100 bin km, 1 milyon) kilometreye göre düzeltilince ~800 bin eder
        FakeScraper.pages[self.URL] = [listing(1, 800_000, km=200_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=cfg, bench_cfg=BenchConfig(km_tolerance_pct=120))
        self.assertEqual(self.sent, [])

    async def test_same_price_with_comparable_km_is_a_deal(self):
        self.lists()
        cfg = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=1, release_minutes=0)
        FakeScraper.pages[self.URL] = [listing(1, 800_000, km=100_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=cfg, bench_cfg=BenchConfig())
        self.assertEqual(len(self.sent), 1)

    async def test_adjustment_is_capped(self):
        from src.benchmark import Benchmark
        b = Benchmark(None, None, BenchConfig())
        self.assertEqual(b._adjust(1_000_000, 500_000, 100_000), 1_300_000.0)   # +%30 üst sınır
        self.assertEqual(b._adjust(1_000_000, 100_000, 500_000), 700_000.0)     # -%30 alt sınır
        self.assertEqual(b._adjust(1_000_000, 0, 100_000), 1_000_000)           # km bilinmiyorsa dokunma


class DealerBenchTests(PipelineBase):
    async def test_market_check_uses_arabam_listings_not_perplexity(self):
        FakeScraper.lists[TRIM] = [row(i, 1_000_000) for i in range(1, 7)]
        self.pplx_status = 500                                   # Perplexity çökse bile galerici mesajı gelir
        FakeScraper.pages[self.URL] = [listing(1, 840_000)]
        dealers = parse_dealers({"dealers": [DEALERS["dealers"][1]]})   # market_check açık galerici
        async with self.Session() as s:
            st = await self.cycle(s, modes=("dealer",), dealers=dealers)
        self.assertEqual((st.deferred, self.pplx_calls), (0, 0))
        self.assertEqual([m["chat_id"] for m in self.sent], ["222"])
        self.assertIn("Aynı donanımdaki 6 güncel ilanın ortası", self.sent[0]["text"])


class FilteredUrlTests(PipelineBase):
    def test_year_and_km_filter_added(self):
        from src.benchmark import filtered_url
        u = filtered_url("https://www.arabam.com/ikinci-el/otomobil/renault-megane", 2018, 100_000, 1, 30.0)
        self.assertEqual(u, "https://www.arabam.com/ikinci-el/otomobil/renault-megane"
                            "?minYear=2017&maxYear=2019&minkm=70000&maxkm=130000")
        self.assertIn("&minYear=", filtered_url("https://x/y?sort=DateDesc", 2018, 0, 1, 30.0))
        self.assertNotIn("minkm", filtered_url("https://x/y", 2018, 0, 1, 30.0))


class ProfitRuleTests(PipelineBase):
    CFG = CompareConfig(active_start=0, active_end=24, min_discount_pct=15.0, release_minutes=0,
                        max_discount_pct=30.0, min_profit_tl=150_000, min_profit_pct=8.0)

    async def test_tl_profit_path_and_caps(self):
        FakeScraper.lists[TRIM] = [row(i, 2_000_000) for i in range(1, 12)]
        FakeScraper.pages[self.URL] = [
            listing(1, 1_800_000),   # %10 ve 200 bin TL: TL yolundan geçer
            listing(2, 1_900_000),   # %5, 100 bin TL: geçmez
            listing(3, 1_300_000),   # %35: üst sınırı aşar, geçmez
        ]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual(len(self.sent), 1)
        self.assertIn("1.800.000 TL", self.sent[0]["text"])

    async def test_cheap_car_small_tl_gap_needs_percent(self):
        FakeScraper.lists[TRIM] = [row(i, 400_000) for i in range(1, 12)]
        FakeScraper.pages[self.URL] = [listing(1, 350_000)]   # %12,5 ama 50 bin TL: ne %15 ne TL yolu
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual(self.sent, [])


class SameTrimOnlyTests(PipelineBase):
    CFG = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, release_minutes=0)

    async def test_other_trims_do_not_inflate_the_median(self):
        # motor sayfasında 10 ilan var ama hepsi üst donanım (pahalı): aynı donanım yok -> karşılaştırma yok
        pricey = dict(row(1, 1_400_000), name="Renault Megane 1.5 dCi Icon")
        FakeScraper.lists[ENGINE] = [dict(pricey, id=f"r{i}") for i in range(1, 11)]
        FakeScraper.pages[self.URL] = [listing(1, 1_150_000)]          # üst donanımlara göre %18 ucuz görünürdü
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual(self.sent, [])


class EmptyPageTests(PipelineBase):
    async def test_empty_later_page_is_not_requested_again(self):
        self.URL = "https://www.arabam.com/ikinci-el/otomobil/x?sort=DateDesc&page=9"
        async with self.Session() as s:
            await self.cycle(s)
            await self.cycle(s)
        self.assertEqual(len(FakeScraper.category_fetches), 1)


class DeepPageTests(PipelineBase):
    async def test_deep_pages_are_refreshed_rarely(self):
        self.URL = "https://www.arabam.com/ikinci-el/otomobil/x?sort=DateDesc&page=3"
        FakeScraper.pages[self.URL] = [listing(1, 900_000)]
        async with self.Session() as s:
            await self.cycle(s)
            await self.cycle(s)
        self.assertEqual(len(FakeScraper.category_fetches), 1)


class StrictQualityTests(PipelineBase):
    CFG = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, release_minutes=0)

    async def test_extreme_discount_is_not_a_candidate(self):
        FakeScraper.lists[TRIM] = [row(i, 1_000_000) for i in range(1, 8)]
        FakeScraper.pages[self.URL] = [listing(1, 450_000)]          # %55: dolandırıcılık/hata şüphesi
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual(self.sent, [])

    async def test_old_or_high_km_car_is_not_a_candidate(self):
        # alıcısı olmayan araç: çok eski model ya da çok yüksek km, ne kadar ucuz olursa olsun gitmez
        FakeScraper.lists[TRIM] = [row(i, 1_000_000) for i in range(1, 8)]
        FakeScraper.pages[self.URL] = [listing(1, 800_000, year=2012), listing(2, 800_000, km=291_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
        self.assertEqual(self.sent, [])

    async def test_loose_model_level_is_not_used_by_default(self):
        FakeScraper.lists[BASE] = [row(i, 1_000_000) for i in range(1, 12)]   # sadece tüm model sayfasında veri var
        FakeScraper.pages[self.URL] = [listing(1, 800_000)]
        async with self.Session() as s:
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig())
            self.assertEqual(self.sent, [])                           # varsayılan: donanım ve motor düzeyi
            FakeScraper.pages[self.URL] = [listing(2, 800_000)]
            await self.cycle(s, modes=("compare",), compare=self.CFG, bench_cfg=BenchConfig(levels=3))
        self.assertEqual(len(self.sent), 1)
