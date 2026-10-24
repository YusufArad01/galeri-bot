"""İnternet taraması (Perplexity) tahmin yedeği: yalnız arabam'da emsal yoksa, sıkı eşikle, 'tahmin' etiketiyle."""
import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from src.benchmark import BenchConfig, Benchmark
from src.estimate import PriceService, parse_average
from src.health import CycleStats, Health
from src.maintenance import prune
from src.models.domain import Alert, BotState, EstimateCache, Feedback, Listing
from src.modes.compare import CompareConfig
from src.modes.dealer import parse_dealers
from tests.test_compare import TRIM, listing, row
from tests.test_pipeline import FakeScraper, PipelineBase

CFG = CompareConfig(active_start=0, active_end=24, min_discount_pct=10.0, max_items=1, release_minutes=0)
DEALER = {"dealers": [{"id": "d", "chat_id": "42", "market_check": True, "market_discount_pct": 5,
                       "market_min_profit_tl": 100_000, "rules": [{"brand": "Renault", "model": "Megane"}]}]}


class EstimateBase(PipelineBase):
    """Perplexity sahte sunucusu: self.answer ile dönecek içerik ayarlanır, self.asked istekleri sayar."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.answer = json.dumps({"ortalama": 1_000_000, "min": 900_000, "max": 1_100_000})
        self.status = 200
        self.asked: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.asked.append(json.loads(request.content))
            if self.status != 200:
                return httpx.Response(self.status, json={"error": "x"})
            return httpx.Response(200, json={"choices": [{"message": {"content": self.answer}}]})

        self.pplx_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def asyncTearDown(self):
        await self.pplx_client.aclose()
        await super().asyncTearDown()

    def service(self, session, **kw):
        return PriceService(session, "KEY", client=self.pplx_client, retry_sleep=0, **kw)


class ParseTests(PipelineBase):
    def test_parse_average(self):
        self.assertEqual(parse_average('{"ortalama": 850000, "min": 1, "max": 2}'), 850000.0)
        self.assertEqual(parse_average('```json\n{"ortalama": "850.000"}\n``` [1]'), 850000.0)
        for bad in ('{"ortalama": null}', '{"ortalama": 0}', '{"ortalama": 900}', '{"ortalama": 999999999}', "bilmiyorum", ""):
            self.assertIsNone(parse_average(bad), bad)


class ServiceTests(EstimateBase):
    DATA = {"brand": "Renault", "model": "Megane", "trim": "1.5 dCi Touch", "year": 2018, "km": 100000}

    async def test_asks_once_then_uses_cache_across_cycles(self):
        async with self.Session() as s:
            self.assertEqual(await self.service(s).estimate(self.DATA), 1_000_000)
            self.assertEqual(await self.service(s).estimate(dict(self.DATA, km=110000)), 1_000_000)   # aynı 25 bin km dilimi
            self.assertEqual(len(self.asked), 1)
            self.assertEqual((await s.execute(select(EstimateCache))).scalars().one().average, 1_000_000)
            self.assertEqual(self.asked[0]["model"], "sonar-pro")

    async def test_unknown_answer_is_cached_for_a_day_only(self):
        self.answer = '{"ortalama": null, "min": null, "max": null}'
        async with self.Session() as s:
            self.assertIsNone(await self.service(s).estimate(self.DATA))
            self.assertIsNone(await self.service(s).estimate(self.DATA))
            self.assertEqual(len(self.asked), 1)                       # "bilmiyorum" ikinci kez para harcatmaz
            (await s.execute(select(EstimateCache))).scalars().one().fetched_at = datetime.utcnow() - timedelta(days=2)
            await s.commit()
            await self.service(s).estimate(self.DATA)
            self.assertEqual(len(self.asked), 2)

    async def test_service_errors_return_none_and_are_not_cached(self):
        self.status = 401
        async with self.Session() as s:
            svc = self.service(s)
            self.assertIsNone(await svc.estimate(self.DATA))
            self.assertEqual(svc.last_error_kind, "auth")
            self.assertEqual((await s.execute(select(EstimateCache))).scalars().all(), [])

    async def test_breaker_and_call_limit(self):
        self.status = 500
        async with self.Session() as s:
            svc = self.service(s)
            for year in range(2010, 2018):
                await svc.estimate(dict(self.DATA, year=year))
            self.assertEqual(svc.calls, 3)                              # 3 ardışık hatadan sonra durdu
        self.asked.clear()
        self.status = 200
        async with self.Session() as s:
            svc = self.service(s, max_calls=2)
            for year in range(2010, 2018):
                await svc.estimate(dict(self.DATA, year=year))
            self.assertEqual(len(self.asked), 2)

    async def test_disabled_without_key(self):
        async with self.Session() as s:
            self.assertIsNone(await PriceService(s, "", client=self.pplx_client).estimate(self.DATA))
        self.assertEqual(self.asked, [])


class FallbackRuleTests(EstimateBase):
    """Yedek yalnız emsal yetmezse çalışır ve yalnız %25-%40 farkta mesaj üretir."""

    async def run_compare(self, price, peers=0):
        if peers:
            FakeScraper.lists[TRIM] = [row(i, 1_000_000) for i in range(1, peers + 1)]
        FakeScraper.pages[self.URL] = [listing(1, price)]
        async with self.Session() as s:
            svc = self.service(s)
            st = await self.cycle(s, modes=("compare",), compare=CFG, bench_cfg=BenchConfig(min_comparables=10), estimator=svc)
            alerts = (await s.execute(select(Alert))).scalars().all()
        return st, alerts

    async def test_rare_car_gets_labeled_estimate_message(self):
        st, alerts = await self.run_compare(700_000)                    # tahminden %30 ucuz
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0]["text"]
        for needle in ("Tahmini fiyata göre %30,0 ucuz", "İnternet taraması tahmini: 1.000.000 TL", "Bu bir tahmindir"):
            self.assertIn(needle, text)
        self.assertIn("tahmin", alerts[0].reason)
        self.assertEqual(st.pplx_calls, 1)

    async def test_window_is_25_to_40_percent(self):
        for price in (800_000, 740_000, 550_000):                       # %20 az, %26 ok, %45 fazla
            self.sent.clear()
            FakeScraper.pages[self.URL] = [listing(int(price), price)]
            async with self.Session() as s:
                await self.cycle(s, modes=("compare",), compare=CFG, bench_cfg=BenchConfig(min_comparables=10), estimator=self.service(s))
            self.assertEqual(len(self.sent), 1 if price == 740_000 else 0, price)

    async def test_enough_arabam_peers_never_asks_perplexity(self):
        st, _ = await self.run_compare(700_000, peers=11)
        self.assertEqual((st.pplx_calls, self.asked), (0, []))
        self.assertIn("Aynı donanımdaki 11 güncel ilanın", self.sent[0]["text"])

    async def test_blocked_site_does_not_trigger_estimates(self):
        scraper = FakeScraper(None)
        scraper.last_blocked = True
        async with self.Session() as s:
            bench = Benchmark(s, scraper, BenchConfig(), self.service(s))
            self.assertIsNone(await bench.stats(listing(1, 700_000)))
        self.assertEqual(self.asked, [])

    async def test_dealer_needs_25_percent_not_the_normal_5(self):
        FakeScraper.pages[self.URL] = [listing(1, 700_000), listing(2, 900_000)]   # %30 ve %10 (normal kuralda ikisi de geçerdi)
        async with self.Session() as s:
            await self.cycle(s, modes=("dealer",), dealers=parse_dealers(DEALER), bench_cfg=BenchConfig(min_comparables=10),
                             estimator=self.service(s))
            alerts = (await s.execute(select(Alert))).scalars().all()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("700.000 TL", self.sent[0]["text"])
        self.assertIn("🔮 İnternet taraması tahmini (kesin değil): 1.000.000 TL", self.sent[0]["text"])
        self.assertIn("Bu bir tahmindir", self.sent[0]["text"])
        self.assertIn("tahmin", alerts[0].reason)

    async def test_dealer_with_other_decision_path_does_not_pay_for_estimate(self):
        target = {"dealers": [dict(DEALER["dealers"][0], rules=[{"brand": "Renault", "model": "Megane", "max_price": 800_000}])]}
        model = {"dealers": [dict(DEALER["dealers"][0], rules=[{"brand": "Renault", "model": "Megane", "price_model": {
            "ref_year": 2018, "bands": [[50_000, 90_000, 1_050_000, 1_150_000], [100_000, 150_000, 950_000, 1_050_000]]}}])]}
        urgent = {"dealers": [dict(DEALER["dealers"][0], urgent_words=["galeriden"], rules=[{"brand": "Renault", "model": "Megane"}])]}
        for raw in (target, model, urgent):
            FakeScraper.pages[self.URL] = [listing(1, 700_000, paint_parts=[])]
            async with self.Session() as s:
                await self.cycle(s, modes=("dealer",), dealers=parse_dealers(raw), bench_cfg=BenchConfig(min_comparables=10),
                                 estimator=self.service(s))
            self.assertEqual(self.asked, [], raw)
            self.assertEqual(len(self.sent), 1)
            self.sent.clear()
            async with self.Session() as s:   # sonraki dealer profili aynı ilanı yeniden değerlendirsin
                await s.execute(Alert.__table__.delete())
                await s.execute(Listing.__table__.delete())
                await s.commit()


class HealthTests(EstimateBase):
    async def test_heartbeat_splits_votes_by_source_and_counts_queries(self):
        async with self.Session() as s:
            for i, (reason, vote) in enumerate([("piyasa -%12,0", 1), ("piyasa tahmini -%30,0", 0), ("tahmin -%28,0", 0)], start=1):
                s.add(Alert(id=i, source_listing_id=str(i), mode="dealer", dealer_id="d", chat_id="42", text="t", reason=reason, status="SENT"))
                s.add(Feedback(alert_id=i, dealer_id="d", chat_id="42", vote=vote))
            s.add(BotState(key="totals", value=json.dumps({"since": (datetime.utcnow() - timedelta(days=1, minutes=1)).isoformat()})))
            await s.commit()
            h = Health(s, self.telegram, "ADMIN", heartbeat_hours=24)
            await h.record_cycle(CycleStats(pplx_calls=4))
        text = self.sent[-1]["text"]
        self.assertIn("👍 1 / 👎 2 (tahminli mesajlar: 👍 0 / 👎 2)", text)
        self.assertIn("İnternet tahmini: 4 sorgu", text)

    async def test_admin_warned_once_when_key_or_balance_is_bad(self):
        async with self.Session() as s:
            h = Health(s, self.telegram, "ADMIN", heartbeat_hours=48)
            await h.record_cycle(CycleStats(pplx_error="quota", links_found=5))
            await h.record_cycle(CycleStats(pplx_error="quota", links_found=5))
            await h.record_cycle(CycleStats(pplx_error="rate", links_found=5))   # geçici hata: uyarı yok
        warnings = [m for m in self.sent if "Perplexity" in m["text"]]
        self.assertEqual(len(warnings), 1)
        self.assertIn("bakiye bitmiş", warnings[0]["text"])

    async def test_estimate_cache_is_pruned_after_two_weeks(self):
        async with self.Session() as s:
            now = datetime.utcnow()
            s.add(EstimateCache(cache_key="eski", average=1.0, fetched_at=now - timedelta(days=15)))
            s.add(EstimateCache(cache_key="yeni", average=1.0, fetched_at=now - timedelta(days=3)))
            await s.commit()
            removed = await prune(s, now)
            self.assertEqual(removed["estimate_cache"], 1)
