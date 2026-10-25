import datetime
import random
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.domain import Proxy
from loguru import logger


class ProxyManager:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def seed(self, urls: list[str]) -> None:
        """Tabloyu PROXY_URL'e eşitler: listedekiler eklenir/açılır, listede olmayanlar KAPATILIR. Proxy değiştirilince
        eski (engelli) IP diskteki veritabanında kalıp rastgele seçilmesin."""
        wanted = set(urls)
        known = set()
        for proxy in (await self.session.execute(select(Proxy))).scalars().all():
            known.add(proxy.url)
            if proxy.url in wanted and not proxy.is_active:
                proxy.is_active, proxy.failure_count = True, 0
            elif proxy.url not in wanted and proxy.is_active:
                proxy.is_active = False
                logger.info("PROXY_URL'de olmayan proxy kapatıldı.")
        for url in wanted - known:
            self.session.add(Proxy(url=url))
        await self.session.commit()

    async def has_active(self) -> bool:
        stmt = select(Proxy.id).where(Proxy.is_active == True).limit(1)  # noqa: E712
        return (await self.session.execute(stmt)).first() is not None

    async def get_proxy(self) -> Proxy | None:
        stmt = select(Proxy).where(Proxy.is_active == True).order_by(Proxy.last_used_at.asc().nulls_first())  # noqa: E712
        result = await self.session.execute(stmt)
        candidates = result.scalars().all()[:3]  # en az kullanılan üçü arasından rastgele
        proxy = random.choice(candidates) if candidates else None
        if proxy:
            proxy.last_used_at = datetime.datetime.utcnow()
            await self.session.commit()
        return proxy

    async def mark_success(self, proxy_id: int):
        stmt = select(Proxy).where(Proxy.id == proxy_id)
        result = await self.session.execute(stmt)
        proxy = result.scalars().first()
        if proxy:
            proxy.success_count += 1
            proxy.failure_count = 0  # ardışık hata sayacı
            await self.session.commit()

    async def mark_failure(self, proxy_id: int):
        stmt = select(Proxy).where(Proxy.id == proxy_id)
        result = await self.session.execute(stmt)
        proxy = result.scalars().first()
        if proxy:
            proxy.failure_count += 1
            if proxy.failure_count > 5:
                logger.warning(f"Quarantining proxy {proxy.url} due to repeated failures.")
                proxy.is_active = False
            await self.session.commit()
