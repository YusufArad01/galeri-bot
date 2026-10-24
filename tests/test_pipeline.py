import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
import asyncio

from src.feedback import handle_update, poll_forever
from src.health import CycleStats, Health
from src.maintenance import prune
from src.models.domain import Alert, BenchCache, Base, BotState, Feedback, Listing
from src.modes.dealer import parse_dealers
from src.notifications import Telegram, enqueue_alert, flush_alerts
from src.pipeline import run_cycle


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
    category_fetches: list[str] = []
    blocked = False

    def __init__(self, proxy_manager):
        self.last_blocked = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch_category_links(self, url):
        FakeScraper.category_fetches.append(url)
        if FakeScraper.blocked:
            self.last_blocked = True
            return []
        return [c["url"] for c in FakeScraper.pages.get(url, [])]

    def source_id(self, url):
        return url.rsplit("/", 1)[-1]

    async def fetch_list_rows(self, url):
        FakeScraper.list_fetches.append(url)
        return [dict(r) for r in FakeScraper.lists.get(url.split("?")[0], [])]  # süzgeç parametreleri yok sayılır

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

        self.sent: list[dict] = []        # sendMessage istekleri
        self.calls: list[tuple[str, dict]] = []   # diğer Telegram yöntemleri (answerCallbackQuery...)
        self.telegram_ok = True
        self.telegram_status: dict[str, int] = {}   # chat_id -> zorla HTTP kodu (örn. 403)
        self.pending_updates: list[dict] = []        # bir sonraki getUpdates cevabı
        FakeScraper.pages = {}
        FakeScraper.lists = {}
        FakeScraper.list_fetches = []
        FakeScraper.category_fetches = []
        FakeScraper.blocked = False
        # testler günün saatinden bağımsız çalışsın: galerici gönderim saati her zaman açık
        self._dealer_hours = (settings.DEALER_ACTIVE_START, settings.DEALER_ACTIVE_END)
        settings.DEALER_ACTIVE_START, settings.DEALER_ACTIVE_END = 0, 24
        self._admin = settings.ADMIN_CHAT_ID
        settings.ADMIN_CHAT_ID = "ADMIN"

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if request.url.path.endswith("/sendMessage"):
                if not self.telegram_ok:
                    return httpx.Response(500, json={"ok": False})
                code = self.telegram_status.get(str(body.get("chat_id")), 200)
                if code != 200:
                    return httpx.Response(code, json={"ok": False})
                self.sent.append(body)
                return httpx.Response(200, json={"ok": True})
            if request.url.path.endswith("/getUpdates"):
                res, self.pending_updates = self.pending_updates, []
                return httpx.Response(200, json={"ok": True, "result": res})
            self.calls.append((request.url.path.rsplit("/", 1)[-1], body))
            return httpx.Response(200, json={"ok": True, "result": []})

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.telegram = Telegram("TOKEN", client=self.client)

    async def asyncTearDown(self):
        settings.DEALER_ACTIVE_START, settings.DEALER_ACTIVE_END = self._dealer_hours
        settings.ADMIN_CHAT_ID = self._admin
        await self.client.aclose()
        await self.engine.dispose()
        try:
            os.remove(self.path)
        except OSError:  # Windows: aiosqlite iş parçacığı dosyayı biraz geç bırakabilir
            pass

    async def cycle(self, session, *, modes=("dealer",), dealers=(), stats=None, compare=None, bench_cfg=None, targets=None):
        return await run_cycle(
            session=session, engine_factory=lambda pm: FakeScraper(pm), telegram=self.telegram,
            dealers=list(dealers), targets=targets or [self.URL], modes=set(modes), default_chat_id="ADMIN",
            max_listings=40, jitter=(0, 0), stats=stats, compare=compare, bench_cfg=bench_cfg)

    async def count(self, session, model):
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


DEALERS = {"dealers": [
    {"id": "ahmet", "name": "Ahmet Oto", "chat_id": "111", "market_check": False,
     "rules": [{"brand": "Renault", "model": "Megane 1.5", "year_min": 2016, "max_price": 900000}]},
    {"id": "mehmet", "name": "Mehmet Galeri", "chat_id": "222", "market_check": True, "market_discount_pct": 15,
     "rules": [{"brand": "Renault", "model": "Megane"}, {"brand": "Fiat", "model": "Egea", "max_price": 700000}]},
]}


class DealerModeTests(PipelineBase):
    async def test_target_price_hits_and_no_duplicates(self):
        FakeScraper.pages[self.URL] = [
            car(40, 880_000),   # Ahmet hedefin altında
            car(41, 840_000),   # Ahmet hedefin altında
            car(42, 950_000),   # Ahmet hedefin üstünde, Mehmet'in Megane kuralında fiyat şartı yok -> kimseye yok
            car(43, 650_000, brand="Fiat", model="Egea", trim="1.4", title="Galeriden Fiat Egea 1.4 2019 Model"),
        ]
        dealers = parse_dealers(DEALERS)
        async with self.Session() as s:
            st = await self.cycle(s, dealers=dealers)
            by_chat: dict[str, list[str]] = {}
            for m in self.sent:
                by_chat.setdefault(m["chat_id"], []).append(m["text"])
            self.assertEqual(len(by_chat["111"]), 2)   # Ahmet: 40 ve 41
            self.assertEqual(len(by_chat["222"]), 1)   # Mehmet: 43 (Egea hedef fiyatı)
            self.assertIn("Hedef: Fiat Egea ≤ 700.000 TL", by_chat["222"][0])
            self.assertNotIn("ADMIN", by_chat)
            self.assertEqual(st.alerts_sent, 3)

            await self.cycle(s, dealers=dealers)       # tekrar çalıştırınca mükerrer mesaj yok
            self.assertEqual(len(self.sent), 3)

    async def test_damaged_car_only_for_dealers_who_allow_it(self):
        raw = json.loads(json.dumps(DEALERS))
        raw["dealers"][0]["allow_damaged"] = True
        FakeScraper.pages[self.URL] = [car(60, 700_000, is_heavy_damage=True, is_disqualified=True,
                                           damage_reasons=["3 değişen"])]
        async with self.Session() as s:
            await self.cycle(s, dealers=parse_dealers(raw))
        self.assertEqual([m["chat_id"] for m in self.sent], ["111"])
        self.assertIn("3 değişen", self.sent[0]["text"])

    async def test_phone_is_shown_when_known(self):
        FakeScraper.pages[self.URL] = [car(61, 700_000, phone="0507 257 83 99")]
        async with self.Session() as s:
            await self.cycle(s, dealers=parse_dealers({"dealers": [DEALERS["dealers"][0]]}))
        self.assertIn("📞 0507 257 83 99", self.sent[0]["text"])

    async def test_strong_deal_is_marked_in_reason(self):
        FakeScraper.pages[self.URL] = [car(62, 700_000)]
        d = dict(DEALERS["dealers"][0], strong_tl=150_000)
        d["rules"] = [dict(d["rules"][0], max_price=900000, baseline=1_000_000)]
        async with self.Session() as s:
            await self.cycle(s, dealers=parse_dealers({"dealers": [d]}))
            reason = (await s.execute(select(Alert.reason))).scalar_one()
        self.assertFalse(reason.startswith("★"))   # fark yalnız hedef fiyata göre: emsal/model farkı yok -> güçlü sayılmaz


class OutboxTests(PipelineBase):
    DEALER = {"dealers": [DEALERS["dealers"][0]]}

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
                st = await self.cycle(s, dealers=parse_dealers(self.DEALER))
                self.assertEqual((st.alerts_queued, st.alerts_sent), (1, 0))
                alert = (await s.execute(select(Alert))).scalars().one()
                self.assertEqual(alert.status, "PENDING")

                self.telegram_ok = True
                st2 = await self.cycle(s, dealers=parse_dealers(self.DEALER))  # hepsi bilinen; outbox yine de boşalır
                self.assertEqual(st2.alerts_sent, 1)
                self.assertEqual((await s.execute(select(Alert))).scalars().one().status, "SENT")
            finally:
                n.asyncio.sleep = orig

    async def test_message_has_feedback_buttons(self):
        FakeScraper.pages[self.URL] = [car(21, 800_000)]
        async with self.Session() as s:
            await self.cycle(s, dealers=parse_dealers(self.DEALER))
            alert_id = (await s.execute(select(Alert.id))).scalar_one()
        buttons = self.sent[0]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["callback_data"] for b in buttons], [f"f:{alert_id}:1", f"f:{alert_id}:0"])

    async def test_permanent_rejection_fails_alert_and_tells_admin_once(self):
        self.telegram_status["111"] = 403          # galerici botu başlatmamış
        FakeScraper.pages[self.URL] = [car(22, 800_000), car(23, 810_000)]
        async with self.Session() as s:
            await self.cycle(s, dealers=parse_dealers(self.DEALER))
            statuses = [a.status for a in (await s.execute(select(Alert))).scalars().all()]
        self.assertEqual(statuses, ["FAILED", "FAILED"])
        admin = [m for m in self.sent if m["chat_id"] == "ADMIN"]
        self.assertEqual(len(admin), 1)                         # iki mesaj, tek uyarı (soğuma süresi)
        self.assertIn("/start", admin[0]["text"])


class FeedbackTests(PipelineBase):
    async def _alert(self, s, chat="111"):
        a = await enqueue_alert(s, source_listing_id="1", mode="dealer", dealer_id="ahmet", chat_id=chat, text="t", reason="r")
        await s.commit()
        return a

    def _cb(self, alert_id, vote, chat=111):
        return {"update_id": 1, "callback_query": {"id": "cb1", "data": f"f:{alert_id}:{vote}",
                                                    "message": {"message_id": 7, "chat": {"id": chat}}}}

    async def test_vote_is_stored_and_buttons_replaced(self):
        async with self.Session() as s:
            a = await self._alert(s)
            await handle_update(s, self.telegram, self._cb(a.id, 1), "ADMIN")
            await handle_update(s, self.telegram, self._cb(a.id, 0), "ADMIN")      # fikir değiştirdi: tek oy, sonuncusu
            rows = (await s.execute(select(Feedback))).scalars().all()
        self.assertEqual([(r.alert_id, r.vote, r.dealer_id) for r in rows], [(a.id, 0, "ahmet")])
        self.assertIn("answerCallbackQuery", [c[0] for c in self.calls])
        self.assertIn("editMessageReplyMarkup", [c[0] for c in self.calls])

    async def test_vote_from_wrong_chat_is_ignored(self):
        async with self.Session() as s:
            a = await self._alert(s, chat="111")
            await handle_update(s, self.telegram, self._cb(a.id, 1, chat=999), "ADMIN")
            self.assertEqual(await self.count(s, Feedback), 0)

    async def test_start_replies_with_chat_id_and_tells_admin(self):
        upd = {"update_id": 2, "message": {"text": "/start", "chat": {"id": 555, "first_name": "Ali", "username": "ali_oto"}}}
        async with self.Session() as s:
            await handle_update(s, self.telegram, upd, "ADMIN")
        by_chat = {m["chat_id"]: m["text"] for m in self.sent}
        self.assertIn("555", by_chat["555"])
        self.assertIn("chat_id=555", by_chat["ADMIN"])


class PollingTests(PipelineBase):
    _alert = FeedbackTests._alert
    _cb = FeedbackTests._cb

    async def test_poll_loop_records_vote_and_advances_offset(self):
        async with self.Session() as s:
            a = await self._alert(s)
        self.pending_updates = [dict(self._cb(a.id, 1), update_id=9)]
        task = asyncio.create_task(poll_forever(self.telegram, self.Session, "ADMIN"))
        try:
            for _ in range(60):
                await asyncio.sleep(0.05)
                async with self.Session() as s:
                    if await self.count(s, Feedback):
                        break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self.Session() as s:
            self.assertEqual(await self.count(s, Feedback), 1)
            self.assertEqual((await s.get(BotState, "tg_offset")).value, "10")


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

    async def test_heartbeat_after_interval(self):
        async with self.Session() as s:
            h = Health(s, self.telegram, "ADMIN", heartbeat_hours=0)
            await h.record_cycle(CycleStats(links_found=10, scraped=5, alerts_sent=2))
            self.assertIn("Günlük özet", self.sent[-1]["text"])
            self.assertIn("2 mesaj", self.sent[-1]["text"])
            self.assertIn("👍 0 / 👎 0", self.sent[-1]["text"])

    async def test_page_structure_alert(self):
        async with self.Session() as s:
            h = Health(s, self.telegram, "ADMIN")
            await h.record_cycle(CycleStats(links_found=20, scraped=20, expertise_missing=3))
            self.assertEqual(self.sent, [])                      # birkaç eksik normal
            await h.record_cycle(CycleStats(links_found=20, scraped=20, expertise_missing=12))
            self.assertIn("sayfa yapısı", self.sent[-1]["text"])

    async def test_dealer_summary_once_a_day_at_summary_hour(self):
        dealers = parse_dealers({"dealers": [{"id": "d", "chat_id": "42", "max_price": 1,
                                              "rules": [{"brand": "Renault", "max_price": 1}]},
                                             {"id": "e", "chat_id": "43", "rules": [{"brand": "Fiat", "max_price": 1}]}]})
        day = datetime(2026, 10, 24, 10, 0)    # UTC; Türkiye saatiyle aynı gün
        async with self.Session() as s:
            for i, reason in enumerate(["★ piyasa", "hedef", "★ acil"]):
                s.add(Alert(source_listing_id=str(i), mode="dealer", dealer_id="d", chat_id="42", text="t",
                            reason=reason, status="SENT", sent_at=day))
            await s.commit()
            h = Health(s, self.telegram, "ADMIN")
            self.assertEqual(await h.send_dealer_summaries(dealers, now=datetime(2026, 10, 24, 12, 0)), 0)   # 15:00 TR: saati değil
            self.assertEqual(await h.send_dealer_summaries(dealers, now=datetime(2026, 10, 24, 19, 30)), 2)  # 22:30 TR
            self.assertEqual(await h.send_dealer_summaries(dealers, now=datetime(2026, 10, 24, 19, 50)), 0)  # aynı gün tekrar yok
        by_chat = {m["chat_id"]: m["text"] for m in self.sent}
        self.assertIn("3 ilan gönderdim; 2 tanesi güçlü", by_chat["42"])
        self.assertIn("yeni ilan çıkmadı", by_chat["43"])


class MaintenanceTests(PipelineBase):
    async def test_prune_removes_old_rows_once_a_day(self):
        now = datetime(2026, 10, 24, 12, 0)
        async with self.Session() as s:
            s.add(BenchCache(cache_key="eski", payload="[]", fetched_at=now - timedelta(days=3)))
            s.add(BenchCache(cache_key="yeni", payload="[]", fetched_at=now - timedelta(hours=1)))
            s.add(Alert(source_listing_id="1", mode="dealer", dealer_id="d", chat_id="1", text="t", status="SENT",
                        created_at=now - timedelta(days=200)))
            s.add(Alert(source_listing_id="2", mode="dealer", dealer_id="d", chat_id="1", text="t", status="PENDING",
                        created_at=now - timedelta(days=200)))
            await s.commit()
            removed = await prune(s, now)
            self.assertEqual((removed["bench_cache"], removed["alerts"]), (1, 1))     # bekleyen mesaj silinmez
            self.assertEqual(await self.count(s, BenchCache), 1)
            self.assertEqual(await prune(s, now), {})                                # aynı gün ikinci kez çalışmaz


if __name__ == "__main__":
    unittest.main()
