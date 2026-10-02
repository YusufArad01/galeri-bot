import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "arbitrage_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    
    REDIS_URL: str = "redis://localhost:6379/0"
    
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    
    TARGET_URL: str = ""
    
    LOG_LEVEL: str = "INFO"

    @property
    def database_url(self) -> str:
        # Default to local file, but allow override for Railway Persistent Volumes (e.g., /data/arbitrage_db.sqlite)
        db_path = os.environ.get("DB_PATH", "arbitrage_db.sqlite")
        return f"sqlite+aiosqlite:///{db_path}"
        
    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }

settings = Settings()
