"""Persists scraped listings and flags those priced below the market average."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_session
from core.logger import logger
from models.listing import ListingModel, MarketStatsModel, OpportunityModel, OpportunityStatus

_CENT: Final[Decimal] = Decimal("0.01")
_HUNDRED: Final[Decimal] = Decimal("100")

MarketKey = tuple[str, str, int]


class ScrapedListing(BaseModel):
    """Validated shape of a listing produced by the scraper."""

    model_config = ConfigDict(str_strip_whitespace=True, frozen=True)

    source_listing_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    brand: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    year: int = Field(ge=1950, le=2100)
    km: int | None = Field(default=None, ge=0, le=5_000_000)
    price: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    city: str | None = Field(default=None, max_length=128)
    url: str = Field(min_length=1, max_length=1024)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    average_price: Decimal
    sample_size: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnalyzerEngine:
    """Deduplicates, stores and scores listings against market averages."""

    def __init__(
        self,
        deviation_threshold: float | None = None,
        min_sample_size: int | None = None,
        stats_ttl_minutes: int | None = None,
    ) -> None:
        threshold = settings.PRICE_DEVIATION_THRESHOLD if deviation_threshold is None else deviation_threshold
        self.threshold: Decimal = Decimal(str(threshold))
        self.min_sample_size: int = settings.MIN_SAMPLE_SIZE if min_sample_size is None else min_sample_size
        ttl = settings.MARKET_STATS_TTL_MINUTES if stats_ttl_minutes is None else stats_ttl_minutes
        self.stats_ttl: timedelta = timedelta(minutes=ttl)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def process_and_analyze(self, listings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Persist new listings and return payloads for newly created opportunities.

        Everything runs in one transaction; each insert uses a savepoint so a
        concurrent duplicate only discards that single listing.
        """
        validated = self._validate(listings)
        if not validated:
            logger.info("Analyzer: nothing to process")
            return []

        opportunities: list[dict[str, Any]] = []
        inserted = 0
        duplicates = 0
        market_cache: dict[MarketKey, MarketSnapshot | None] = {}

        try:
            async with get_session() as session:
                known_ids = await self._existing_source_ids(session, [item.source_listing_id for item in validated])

                for item in validated:
                    if item.source_listing_id in known_ids:
                        duplicates += 1
                        continue
                    known_ids.add(item.source_listing_id)

                    # Market average is resolved before the insert so the listing never skews its own benchmark.
                    key: MarketKey = (item.brand, item.model, item.year)
                    if key not in market_cache:
                        market_cache[key] = await self._get_market_snapshot(session, *key)
                    market = market_cache[key]

                    listing = await self._persist_listing(session, item)
                    if listing is None:
                        duplicates += 1
                        continue
                    inserted += 1

                    if market is None:
                        continue

                    payload = await self._evaluate(session, listing, market)
                    if payload is not None:
                        opportunities.append(payload)
        except SQLAlchemyError:
            logger.exception("Analyzer transaction failed; batch rolled back")
            return []

        logger.info(
            "Analyzer: {} received, {} valid, {} new, {} duplicates, {} opportunities",
            len(listings),
            len(validated),
            inserted,
            duplicates,
            len(opportunities),
        )
        return opportunities

    @staticmethod
    async def mark_opportunities_notified(opportunity_ids: Sequence[int]) -> None:
        if not opportunity_ids:
            return
        try:
            async with get_session() as session:
                await session.execute(
                    update(OpportunityModel)
                    .where(
                        OpportunityModel.id.in_(opportunity_ids),
                        OpportunityModel.status == OpportunityStatus.NEW,
                    )
                    .values(status=OpportunityStatus.NOTIFIED)
                )
        except SQLAlchemyError:
            logger.exception("Failed to mark opportunities {} as notified", list(opportunity_ids))

    @staticmethod
    async def expire_stale_opportunities(max_age_hours: int | None = None) -> int:
        hours = settings.OPPORTUNITY_EXPIRY_HOURS if max_age_hours is None else max_age_hours
        cutoff = _utcnow() - timedelta(hours=hours)
        try:
            async with get_session() as session:
                result = await session.execute(
                    update(OpportunityModel)
                    .where(
                        OpportunityModel.created_at < cutoff,
                        OpportunityModel.status != OpportunityStatus.EXPIRED,
                    )
                    .values(status=OpportunityStatus.EXPIRED)
                    .returning(OpportunityModel.id)
                )
                expired = len(result.all())
        except SQLAlchemyError:
            logger.exception("Failed to expire stale opportunities")
            return 0
        if expired:
            logger.info("Expired {} opportunities older than {}h", expired, hours)
        return expired

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate(listings: Sequence[Mapping[str, Any]]) -> list[ScrapedListing]:
        valid: list[ScrapedListing] = []
        for raw in listings:
            try:
                valid.append(ScrapedListing.model_validate(dict(raw)))
            except ValidationError as exc:
                logger.debug(
                    "Rejected listing {}: {}",
                    raw.get("source_listing_id", "<unknown>"),
                    "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
                )
        return valid

    @staticmethod
    async def _existing_source_ids(session: AsyncSession, source_ids: list[str]) -> set[str]:
        if not source_ids:
            return set()
        result = await session.execute(
            select(ListingModel.source_listing_id).where(ListingModel.source_listing_id.in_(source_ids))
        )
        return set(result.scalars().all())

    async def _get_market_snapshot(
        self, session: AsyncSession, brand: str, model: str, year: int
    ) -> MarketSnapshot | None:
        """Return cached stats if fresh; otherwise recompute from listings and upsert."""
        now = _utcnow()
        cached = (
            await session.execute(
                select(MarketStatsModel).where(
                    MarketStatsModel.brand == brand,
                    MarketStatsModel.model == model,
                    MarketStatsModel.year == year,
                )
            )
        ).scalar_one_or_none()

        if (
            cached is not None
            and cached.updated_at >= now - self.stats_ttl
            and cached.sample_size >= self.min_sample_size
        ):
            return MarketSnapshot(Decimal(cached.average_price), cached.sample_size)

        avg_price, sample_size = (
            await session.execute(
                select(func.avg(ListingModel.price), func.count(ListingModel.id)).where(
                    ListingModel.brand == brand,
                    ListingModel.model == model,
                    ListingModel.year == year,
                )
            )
        ).one()

        if not sample_size or avg_price is None:
            logger.debug("No market data yet for {} {} {}", brand, model, year)
            return None

        average = Decimal(avg_price).quantize(_CENT, rounding=ROUND_HALF_UP)
        upsert = pg_insert(MarketStatsModel).values(
            brand=brand,
            model=model,
            year=year,
            average_price=average,
            sample_size=int(sample_size),
            updated_at=now,
        )
        upsert = upsert.on_conflict_do_update(
            constraint="uq_market_stats_brand_model_year",
            set_={
                "average_price": upsert.excluded.average_price,
                "sample_size": upsert.excluded.sample_size,
                "updated_at": upsert.excluded.updated_at,
            },
        )
        await session.execute(upsert)

        if sample_size < self.min_sample_size:
            logger.debug(
                "Insufficient sample for {} {} {} ({}/{}); skipping deviation check",
                brand,
                model,
                year,
                sample_size,
                self.min_sample_size,
            )
            return None
        return MarketSnapshot(average, int(sample_size))

    @staticmethod
    async def _persist_listing(session: AsyncSession, item: ScrapedListing) -> ListingModel | None:
        listing = ListingModel(
            source_listing_id=item.source_listing_id,
            title=item.title,
            brand=item.brand,
            model=item.model,
            year=item.year,
            km=item.km,
            price=item.price,
            city=item.city,
            url=item.url,
            scraped_at=_utcnow(),
        )
        try:
            async with session.begin_nested():
                session.add(listing)
                await session.flush()
        except IntegrityError:
            logger.debug("Listing {} inserted concurrently by another worker; skipping", item.source_listing_id)
            return None
        return listing

    async def _evaluate(
        self, session: AsyncSession, listing: ListingModel, market: MarketSnapshot
    ) -> dict[str, Any] | None:
        deviation = (market.average_price - listing.price) / market.average_price
        if deviation < self.threshold:
            return None

        deviation_pct = (deviation * _HUNDRED).quantize(_CENT, rounding=ROUND_HALF_UP)
        opportunity = OpportunityModel(
            listing_id=listing.id,
            market_average_price=market.average_price,
            deviation_percentage=deviation_pct,
            status=OpportunityStatus.NEW,
        )
        session.add(opportunity)
        await session.flush()

        logger.success(
            "Opportunity #{}: {} {} {} at {} TL vs market {} TL (-{}%, n={})",
            opportunity.id,
            listing.brand,
            listing.model,
            listing.year,
            listing.price,
            market.average_price,
            deviation_pct,
            market.sample_size,
        )
        return {
            "opportunity_id": opportunity.id,
            "source_listing_id": listing.source_listing_id,
            "title": listing.title,
            "brand": listing.brand,
            "model": listing.model,
            "year": listing.year,
            "km": listing.km,
            "city": listing.city,
            "price": listing.price,
            "market_average_price": market.average_price,
            "deviation_percentage": deviation_pct,
            "sample_size": market.sample_size,
            "url": listing.url,
        }
