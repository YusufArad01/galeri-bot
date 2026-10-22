from sqlalchemy import select

from src.benchmark import BenchConfig
from src.config import Settings
from src.health import CycleStats, Health
from src.models.domain import Listing
from src.modes.compare import CompareConfig
from src.parsing import bench_urls_from_crumbs, parse_list_row
from tests.test_pipeline import FakeScraper, PipelineBase, car

BASE = "https://www.arabam.com/ikinci-el/otomobil/renault-megane"
TRIM = BASE + "-1-5-dci-touch"
ENGINE = BASE + "-1-5-dci"
CRUMBS = ["/ikinci-el/otomobil/renault", "/ikinci-el/otomobil/renault-megane",
          "/ikinci-el/otomobil/renault-megane-1-5-dci", "/ikinci-el/otomobil/renault-megane-1-5-dci-touch"]


def row(i, price, year=2018, km=100000):
    return {"id": f"r{i}", "url": f"https://www.arabam.com/ilan/x/r{i}", "name": "n", "title": "t",
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
    CFG = CompareConfig(min_discount_pct=10.0, max_items=1)

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
            self.assertIn("(Bu turda 3 ilan karşılaştırıldı)", text)
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
        self.assertEqual((self.sent, st.bench_empty), ([], 3))

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
