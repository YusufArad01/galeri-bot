from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from src.config import settings

# timeout: tarama döngüsü ile Telegram dinleyicisi aynı SQLite dosyasına yazar; kilit olursa 30 sn bekle
engine = create_async_engine(settings.database_url, echo=False, connect_args={"timeout": 30})
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()
