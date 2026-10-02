from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import relationship
from src.database import Base

class Proxy(Base):
    __tablename__ = "proxies"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    last_used_at = Column(DateTime, nullable=True)

class Listing(Base):
    __tablename__ = "listings"
    id = Column(Integer, primary_key=True, index=True)
    source_listing_id = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    brand = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    km = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    city = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    url = Column(String, nullable=False)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index('ix_listing_bmy', 'brand', 'model', 'year'),)

class MarketStats(Base):
    __tablename__ = "market_stats"
    id = Column(Integer, primary_key=True, index=True)
    brand = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    average_price = Column(Float, nullable=False)
    sample_size = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Opportunity(Base):
    __tablename__ = "opportunities"
    id = Column(Integer, primary_key=True, index=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False, unique=True)
    market_average_price = Column(Float, nullable=False)
    deviation_percentage = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="NEW") # NEW, NOTIFIED, DISQUALIFIED, EXPIRED
    created_at = Column(DateTime, default=datetime.utcnow)

    listing = relationship("Listing")
