import datetime
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.domain import Proxy
from loguru import logger

class ProxyManager:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_proxy(self) -> Proxy | None:
        stmt = select(Proxy).where(Proxy.is_active == True).order_by(Proxy.last_used_at.asc().nulls_first())
        result = await self.session.execute(stmt)
        proxy = result.scalars().first()
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
