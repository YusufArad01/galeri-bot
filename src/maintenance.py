"""Günlük veri temizliği: veritabanı (Railway volume) sonsuza kadar büyümesin."""
from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import delete

from src.models.domain import Alert, BenchCache, BotState, CompareCandidate, Feedback, Listing

# Mesajlar 180 gün tutulur: tekrar gönderimi (mükerrer engeli) bu süre boyunca korur. İlanlar 90 gün.
_RULES = (
    (BenchCache, BenchCache.fetched_at, timedelta(days=2)),
    (CompareCandidate, CompareCandidate.created_at, timedelta(days=2)),
    (Listing, Listing.scraped_at, timedelta(days=90)),
    (Feedback, Feedback.created_at, timedelta(days=180)),
)


async def prune(session, now: datetime | None = None) -> dict[str, int]:
    """Günde bir kez çalışır (BotState işareti). Silinen satır sayılarını döner; bugün zaten çalıştıysa boş."""
    now = now or datetime.utcnow()
    day = now.date().isoformat()
    mark = await session.get(BotState, "last_prune")
    if mark and mark.value == day:
        return {}
    removed: dict[str, int] = {}
    for model, column, age in _RULES:
        res = await session.execute(delete(model).where(column < now - age))
        removed[model.__tablename__] = res.rowcount or 0
    res = await session.execute(delete(Alert).where(Alert.created_at < now - timedelta(days=180), Alert.status != "PENDING"))
    removed["alerts"] = res.rowcount or 0
    if mark:
        mark.value = day
    else:
        session.add(BotState(key="last_prune", value=day))
    await session.commit()
    logger.info(f"Veri temizliği: {removed}")
    return removed
