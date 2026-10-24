from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, Index, Text, UniqueConstraint
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
    # Hasarlı/elenmiş ilanlar karşılaştırma havuzunu bozmasın diye işaretlenir
    is_disqualified = Column(Boolean, nullable=False, default=False, server_default="0")
    scraped_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index('ix_listing_bmy', 'brand', 'model', 'year'),)


class Alert(Base):
    """Giden Telegram mesajları (outbox). Gönderim başarısız olursa bir sonraki döngüde tekrar denenir."""
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, index=True)
    source_listing_id = Column(String, nullable=False, index=True)
    mode = Column(String, nullable=False)          # compare | dealer
    dealer_id = Column(String, nullable=False, default="")
    chat_id = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    reason = Column(String, nullable=False, default="")   # "★ " ile başlıyorsa güçlü fırsat
    status = Column(String, nullable=False, default="PENDING")  # PENDING, SENT, FAILED
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("source_listing_id", "mode", "dealer_id", name="uq_alert_target"),)


class Feedback(Base):
    """Galericinin mesajdaki 👍/👎 düğmesine verdiği cevap. Alert başına tek oy (sonuncusu geçerli)."""
    __tablename__ = "feedback"
    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, nullable=False, unique=True, index=True)
    dealer_id = Column(String, nullable=False, default="")
    chat_id = Column(String, nullable=False)
    vote = Column(Integer, nullable=False)          # 1 = beğendim, 0 = beğenmedim
    created_at = Column(DateTime, default=datetime.utcnow)


class BotState(Base):
    """Küçük anahtar-değer deposu: sağlık sayaçları, uyarı soğuma süreleri, günlük işaretler."""
    __tablename__ = "bot_state"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BenchCache(Base):
    """Arabam liste sayfalarından okunan 'benzer ilanlar' (karşılaştırma verisi) önbelleği."""
    __tablename__ = "bench_cache"
    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, nullable=False, index=True)  # sayfa adresi
    payload = Column(Text, nullable=False)                               # JSON satır listesi
    fetched_at = Column(DateTime, default=datetime.utcnow)


class CompareCandidate(Base):
    """Genel modun aday havuzu: eşiği geçen ilanlar, düzenli aralıkla en iyisi gönderilir."""
    __tablename__ = "compare_candidates"
    id = Column(Integer, primary_key=True, index=True)
    source_listing_id = Column(String, unique=True, nullable=False, index=True)
    score = Column(Float, nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="NEW")  # NEW, RELEASED, EXPIRED
    created_at = Column(DateTime, default=datetime.utcnow)
    released_at = Column(DateTime, nullable=True)
