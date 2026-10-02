"""ORM models for proxies, listings, market statistics and opportunities."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class OpportunityStatus(str, enum.Enum):
    NEW = "NEW"
    NOTIFIED = "NOTIFIED"
    EXPIRED = "EXPIRED"


class ProxyModel(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true(), index=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ProxyModel id={self.id} active={self.is_active} failures={self.failure_count}>"


class ListingModel(Base):
    __tablename__ = "listings"
    __table_args__ = (Index("ix_listings_brand_model_year", "brand", "model", "year"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_listing_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    brand: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    opportunity: Mapped[OpportunityModel | None] = relationship(
        back_populates="listing",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ListingModel {self.source_listing_id} {self.brand} {self.model} {self.year} {self.price}>"


class MarketStatsModel(Base):
    __tablename__ = "market_stats"
    __table_args__ = (UniqueConstraint("brand", "model", "year", name="uq_market_stats_brand_model_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    average_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<MarketStatsModel {self.brand} {self.model} {self.year} avg={self.average_price} n={self.sample_size}>"


class OpportunityModel(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    market_average_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Stored as a percentage, e.g. 12.50 means the listing is 12.5% below market.
    deviation_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    status: Mapped[OpportunityStatus] = mapped_column(
        Enum(OpportunityStatus, name="opportunity_status"),
        nullable=False,
        default=OpportunityStatus.NEW,
        server_default=OpportunityStatus.NEW.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    listing: Mapped[ListingModel] = relationship(back_populates="opportunity", lazy="joined")

    def __repr__(self) -> str:
        return f"<OpportunityModel id={self.id} listing_id={self.listing_id} dev={self.deviation_percentage}% {self.status}>"
