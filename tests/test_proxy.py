import unittest

from sqlalchemy import select

from src.models.domain import Proxy
from src.proxy.manager import ProxyManager
from src.scraper.engine import split_proxy
from tests.test_pipeline import PipelineBase


class SplitProxyTests(unittest.TestCase):
    def test_with_credentials(self):
        self.assertEqual(split_proxy("http://kul:sifre@1.2.3.4:8080"),
                         {"server": "http://1.2.3.4:8080", "username": "kul", "password": "sifre"})

    def test_special_characters_in_password(self):
        r = split_proxy("http://kul:p%40ss%3Aw@proxy.example.com:3128")
        self.assertEqual((r["username"], r["password"], r["server"]), ("kul", "p@ss:w", "http://proxy.example.com:3128"))

    def test_without_credentials_or_scheme(self):
        self.assertEqual(split_proxy("http://1.2.3.4:8080"), {"server": "http://1.2.3.4:8080"})
        self.assertEqual(split_proxy("1.2.3.4:8080"), {"server": "http://1.2.3.4:8080"})

    def test_socks(self):
        self.assertEqual(split_proxy("socks5://u:p@h:1080")["server"], "socks5://h:1080")


class SeedTests(PipelineBase):
    async def test_seed_replaces_old_proxy(self):
        old, new = "http://u:p@1.1.1.1:1", "http://u:p@2.2.2.2:2"
        async with self.Session() as s:
            pm = ProxyManager(s)
            await pm.seed([old])
            await pm.seed([new])                                    # proxy değişti: eskisi kapanmalı
            self.assertEqual([p.url for p in await _active(s)], [new])
            for _ in range(10):
                self.assertEqual((await pm.get_proxy()).url, new)  # eski IP bir daha seçilmez
            await pm.seed([old, new])                               # ikisi de listede: ikisi de açık
            self.assertEqual(len(await _active(s)), 2)
            await pm.seed([])                                       # liste boş: doğrudan bağlantı
            self.assertEqual((await pm.has_active(), await pm.get_proxy()), (False, None))


class RotateOnBlockTests(PipelineBase):
    async def test_rotate_picks_standby(self):
        """Aktif proxy engeldeyken yedek varsa geçiş yapılır."""
        async with self.Session() as s:
            pm = ProxyManager(s)
            await pm.seed(["http://a:a@1.1.1.1:1"])
            s.add(Proxy(url="http://b:b@2.2.2.2:2", is_active=False, failure_count=0))
            await s.commit()
            new = await pm.rotate_on_block()
            self.assertEqual(new, "http://b:b@2.2.2.2:2")
            active = await _active(s)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].url, "http://b:b@2.2.2.2:2")

    async def test_rotate_no_standby_returns_none(self):
        """Yedek proxy yoksa None döner, aktif kapanmaz."""
        async with self.Session() as s:
            pm = ProxyManager(s)
            await pm.seed(["http://a:a@1.1.1.1:1"])
            self.assertIsNone(await pm.rotate_on_block())
            self.assertEqual(len(await _active(s)), 1)

    async def test_rotate_picks_least_failures(self):
        """Birden fazla yedek varsa en az hatalısı seçilir."""
        async with self.Session() as s:
            pm = ProxyManager(s)
            await pm.seed(["http://a:a@1.1.1.1:1"])
            s.add(Proxy(url="http://b:b@2.2.2.2:2", is_active=False, failure_count=5))
            s.add(Proxy(url="http://c:c@3.3.3.3:3", is_active=False, failure_count=1))
            await s.commit()
            new = await pm.rotate_on_block()
            self.assertEqual(new, "http://c:c@3.3.3.3:3")


async def _active(session):
    return (await session.execute(select(Proxy).where(Proxy.is_active == True))).scalars().all()  # noqa: E712


if __name__ == "__main__":
    unittest.main()
