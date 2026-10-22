import json
import os
import tempfile
import unittest

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.health import CycleStats, Health
from src.models.domain import Alert, Base, BotState, Listing
from src.modes.dealer import parse_dealers
from src.notifications import Telegram
from src.pipeline import run_cycle
from src.pricing.perplexity import PriceService


def car(i, price, **kw):
    d = {"source_listing_id": str(i), "title": f"Galeriden Renault Megane 1.5 dCi Touch 2018 Model Denizli 100.000 KM",
         "description": "", "url": f"https://www.arabam.com/ilan/x/{i}", "price": float(price), "brand": "Renault",
         "model": "Megane", "trim": "1.5 dCi Touch", "year": 2018, "km": 100000, "city": "Denizli",
         "seller_type": "Galeriden", "is_heavy_damage": False, "damage_reasons": [], "is_old_cheap": False,
         "is_disqualified": False}
    d.update(kw)
    return d


class FakeScraper:
    pages: dict[str, list[dict]] = {}
    lists: dict[str, list[dict]] = {}   # karşılaştırma sayfaları: url -> satırlar
    list_fetches: list[str] = []
    blocked = False

    def __init__(self, proxy_manager):
        self.last_blocked = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch_category_links(self, url):
        if FakeScraper.blocked:
            self.last_blocked = True
            return []
        return [c["url"] for c in FakeScraper.pages.get(url, [])]

    def source_id(self, url):
        return url.rsplit("/", 1)[-1]

    async def fetch_list_rows(self, url):
        FakeScraper.list_fetches.append(url)
        return [dict(r) for r in FakeScraper.lists.get(url, [])]

    async def fetch_listing_data(self, url):
        for cars in FakeScraper.pages.values():
            for c in cars:
                if c["url"] == url:
                    return dict(c)
        raise ValueError("not found")


class PipelineBase(unittest.IsolatedAsyncioTestCase):
    URL = "https://www.arabam.com/ikinci-el/otomobil?sort=DateDesc"

    async def asyncSetUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

        self.sent: list[dict] = []
        self.telegram_ok = True
        self.pplx_status = 200
        self.pplx_avg = 1_000_000
        self.pplx_calls = 0
        FakeScraper.pages = {}
        FakeScraper.lists = {}
        FakeScraper.list_fetches = []
        FakeScraper.blocked = False

        def handler(request: httpx.Request) -> httpx.Response:
            if "telegram.org" in request.url.host:
                if not self.telegram_ok:
                    return httpx.Response(500, json={"ok": False})
                self.sent.append(json.loads(request.content))
                return httpx.Response(200, json={"ok": True})
            self.pplx_calls += 1
            if self.pplx_status != 200:
                return httpx.Response(self.pplx_status, json={"error": "x"})
            body = json.dumps({"ortalama": self.pplx_avg, "min": self.pplx_avg - 1, "max": self.pplx_avg + 1})
            return httpx.Response(200, json={"choices": [{"message": {"content": body}}]})

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.telegram = Telegram("TOKEN", client=self.client)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        try:
            os.remove(self.path)
        except OSError:  # Windows: aiosqlite iş parçacığı dosyayı biraz geç bırakabilir
            pass

    async def cycle(self, session, *, modes=("market",), dealers=(), key="k", stats=None, compare=None, bench_cfg=None):
        prices = PriceService(session, key, client=self.client, retry_sleep=0)
        return await run_cycle(
            session=session, engine_factory=lambda kind, pm: FakeScraper(pm), telegram=self.telegram,
            prices=prices, dealers=list(dealers), targets=[self.URL], modes=set(modes), market_chat_id="ADMIN",
            threshold_pct=15.0, suspicious_pct=45.0, max_listings=40, jitter=(0, 0), stats=stats, compare=compare, bench_cfg=bench_cfg)

    async def count(self, session, model):
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


class MarketModeTests(PipelineBase):
    async def test_threshold_dedupe_and_filters(self):
        FakeScraper.pages[self.URL] = [
            car(1, 840_000),                               # %16 altında -> bildirim
            car(2, 860_000),                               # %14 altında -> yok
            car(3, 500_000, is_heavy_damage=True, is_disqualified=True),  # hasarlı -> Perplexity'e bile gitmez
            car(4, 1_300_000),                             # pahalı
        ]
        async with self.Session() as s:
            st = await self.cycle(s)
            self.assertEqual((st.scraped, st.disqualified, st.alerts_queued, st.alerts_sent), (4, 1, 1, 1))
            self.assertEqual(len(self.sent), 1)
            self.assertEqual(self.sent[0]["chat_id"], "ADMIN")
            self.assertIn("%16,0 altında", self.sent[0]["text"])
            self.assertIn("840.000 TL", self.sent[0]["text"])
            self.assertEqual(self.pplx_calls, 1)  # aynı araç tek çağrı (önbellek/memo)
            self.assertEqual(await self.count(s, Listing), 4)

            # ikinci döngü: hepsi bilinen -> yeni çağrı/bildirim yok
            st2 = await self.cycle(s)
            self.assertEqual((st2.scraped, st2.known_skipped, st2.alerts_sent), (0, 4, 0))
            self.assertEqual((self.pplx_calls, len(self.sent)), (1, 1))

    async def test_price_service_failure_does_not_lose_listing(self):
        FakeScraper.pages[self.URL] = [car(10, 800_000)]
        self.pplx_status = 401
        async with self.Session() as s:
            st = await self.cycle(s)
            self.assertEqual((st.deferred, st.price_error_kind, st.alerts_sent), (1, "auth", 0))
            self.assertEqual(await self.count(s, Listing), 0)  # kaydedilmedi -> tekrar denenecek

            self.pplx_status = 200
            st2 = await self.cycle(s)
            self.assertEqual((st2.deferred, st2.alerts_sent), (0, 1))
            self.assertEqual(await self.count(s, Listing), 1)

    async def test_missing_api_key_is_reported_not_silent(self):
        FakeScraper.pages[self.URL] = [car(11, 800_000)]
        async with self.Session() as s:
            st = await self.cycle(s, key="")
            self.assertEqual((st.deferred, st.price_error_kind), (1, "no_key"))
            self.assertEqual(self.pplx_calls, 0)

    async def test_telegram_outage_retries_next_cycle(self):
        FakeScraper.pages[self.URL] = [car(20, 800_000)]
        self.telegram_ok = False
        async with self.Session() as s:
            import src.notifications as n
            orig = n.asyncio.sleep

            async def fast(_):
                return None
            n.asyncio.sleep = fast  # Telegram yeniden deneme beklemelerini atla
            try:
                st = await self.cycle(s)
                self.assertEqual((st.alerts_queued, st.alerts_sent), (1, 0))
                alert = (await s.execute(select(Alert))).scalars().one()
                self.assertEqual(alert.status, "PENDING")

                self.telegram_ok = True
                st2 = await self.cycle(s)  # liste boş değil ama hepsi bilinen; outbox yine de boşalır
                self.assertEqual(st2.alerts_sent, 1)
                self.assertEqual((await s.execute(select(Alert))).scalars().one().status, "SENT")
            finally:
                n.asyncio.sleep = orig

    async def test_suspicious_discount_warning(self):
        FakeScraper.pages[self.URL] = [car(30, 400_000)]  # %60
        async with self.Session() as s:
            await self.cycle(s)
            self.assertIn("Çok yüksek indirim", self.sent[0]["text"])


DEALERS = {"dealers": [
    {"id": "ahmet", "name": "Ahmet Oto", "chat_id": "111", "market_check": False,
     "rules": [{"brand": "Renault", "model": "Megane 1.5", "year_min": 2016, "max_price": 900000}]},
    {"id": "mehmet", "name": "Mehmet Galeri", "chat_id": "222", "market_check": True, "market_discount_pct": 15,
     "rules": [{"brand": "Renault", "model": "Megane"}, {"brand": "Fiat", "model": "Egea", "max_price": 700000}]},
]}


class DealerModeTests(PipelineBase):
    async def test_target_price_and_market_check(self):
        FakeScraper.pages[self.URL] = [
            car(40, 880_000),   # Ahmet hedefinin altında; Mehmet için piyasa 1.0M -> %12 (yetersiz)
            car(41, 840_000),   # Ahmet hedefte; Mehmet için %16 piyasa altı
            car(42, 950_000),   # Ahmet hedefin üstünde; Mehmet %5 -> kimseye yok
            car(43, 650_000, brand="Fiat", model="Egea", trim="1.4", title="Galeriden Fiat Egea 1.4 2019 Model"),
        ]
        dealers = parse_dealers(DEALERS)
        async with self.Session() as s:
            st = await self.cycle(s, modes=("dealer",), dealers=dealers)
            by_chat: dict[str, list[str]] = {}
            for m in self.sent:
                by_chat.setdefault(m["chat_id"], []).append(m["text"])
            self.assertEqual(len(by_chat["111"]), 2)   # Ahmet: 40 ve 41
            self.assertEqual(len(by_chat["222"]), 2)   # Mehmet: 41 (piyasa) ve 43 (hedef fiyat)
            self.assertTrue(any("880.000 TL" in t for t in by_chat["111"]))
            mehmet41 = next(t for t in by_chat["222"] if "840.000 TL" in t)
            self.assertIn("Piyasa ort.", mehmet41)
            mehmet43 = next(t for t in by_chat["222"] if "650.000 TL" in t)
            self.assertIn("Hedef: Fiat Egea ≤ 700.000 TL", mehmet43)
            self.assertNotIn("ADMIN", by_chat)
            self.assertEqual(st.alerts_sent, 4)

            # tekrar çalıştırınca mükerrer mesaj yok
            await self.cycle(s, modes=("dealer",), dealers=dealers)
            self.assertEqual(len(self.sent), 4)

    async def test_target_hit_survives_price_service_outage(self):
        FakeScraper.pages[self.URL] = [car(50, 700_000)]
        self.pplx_status = 500
        dealers = parse_dealers(DEALERS)
        async with self.Session() as s:
            st = await self.cycle(s, modes=("dealer",), dealers=dealers)
        texts = {m["chat_id"]: m["text"] for m in self.sent}
        self.assertEqual(list(texts), ["111"])  # Ahmet piyasa servisine ihtiyaç duymaz
        # Mehmet: hedef fiyatı yok, yalnız piyasa kontrolü var ve servis çöktü -> ilan ertelendi (kaybolmadı)
        self.assertEqual(st.deferred, 1)

        # Servis düzelince ilan yeniden değerlendirilir: Mehmet'e gider, Ahmet'e İKİNCİ kez gitmez
        self.pplx_status = 200
        async with self.Session() as s:
            st2 = await self.cycle(s, modes=("dealer",), dealers=dealers)
        self.assertEqual(st2.deferred, 0)
        self.assertEqual(sorted(m["chat_id"] for m in self.sent), ["111", "222"])

    async def test_damaged_car_only_for_dealers_who_allow_it(self):
        raw = json.loads(json.dumps(DEALERS))
        raw["dealers"][0]["allow_damaged"] = True
        FakeScraper.pages[self.URL] = [car(60, 700_000, is_heavy_damage=True, is_disqualified=True,
                                           damage_reasons=["3 değişen"])]
        self.pplx_status = 500
        async with self.Session() as s:
            await self.cycle(s, modes=("dealer",), dealers=parse_dealers(raw))
        self.assertEqual([m["chat_id"] for m in self.sent], ["111"])
        self.assertIn("3 değişen", self.sent[0]["text"])

    async def test_both_modes(self):
        FakeScraper.pages[self.URL] = [car(70, 800_000)]
        raw = {"dealers": [DEALERS["dealers"][0]]}
        async with self.Session() as s:
            await self.cycle(s, modes=("market", "dealer"), dealers=parse_dealers(raw))
        self.assertEqual(sorted(m["chat_id"] for m in self.sent), ["111", "ADMIN"])


class HealthTests(PipelineBase):
    async def test_blocked_streak_alert_and_recovery(self):
        async with self.Session() as s:
            h = Health(s, self.telegram, "ADMIN", heartbeat_hours=24, blocked_alert_after=2)
            await h.record_cycle(CycleStats(blocked=True))
            self.assertEqual(self.sent, [])                      # 1. döngü: henüz uyarı yok
            await h.record_cycle(CycleStats(blocked=True))
            self.assertEqual(len(self.sent), 1)
            self.assertIn("ilan alınamıyor", self.sent[0]["text"])
            await h.record_cycle(CycleStats(blocked=True))
            self.assertEqual(len(self.sent), 1)                  # soğuma süresi: tekrar spam yok
            await h.record_cycle(CycleStats(links_found=30))
            self.assertIn("düzeldi", self.sent[-1]["text"])

    async def test_price_service_alert(self):
        async with self.Session() as s:
            h = Health(s, self.telegram, "ADMIN")
            await h.record_cycle(CycleStats(links_found=10, price_error_kind="quota", price_failures=1, deferred=5))
            self.assertIn("bakiyesi", self.sent[0]["text"])

    async def test_heartbeat_after_interval(self):
        async with self.Session() as s:
            h = Health(s, self.telegram, "ADMIN", heartbeat_hours=0)
            await h.record_cycle(CycleStats(links_found=10, scraped=5, alerts_sent=2))
            self.assertIn("Günlük özet", self.sent[-1]["text"])
            self.assertIn("2 bildirim", self.sent[-1]["text"])


if __name__ == "__main__":
    unittest.main()
