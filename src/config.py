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
    # Sağlık/uyarı mesajları buraya gider; boşsa TELEGRAM_CHAT_ID kullanılır.
    ADMIN_CHAT_ID: str = ""

    # Taranacak liste sayfası(ları). Birden fazlaysa virgülle ayır.
    TARGET_URL: str = ""

    # --- Çalışma modu -------------------------------------------------------
    # market : Sürüm 1 - ilan fiyatı Perplexity piyasa ortalamasının %X altındaysa bildir
    # dealer : Sürüm 2 - galerici profilleri (marka/model/hedef fiyat) + istenirse piyasa kontrolü
    # both   : ikisi birden
    BOT_MODE: str = "market"

    # --- Sürüm 1 / piyasa kontrolü -----------------------------------------
    DISCOUNT_THRESHOLD_PCT: float = 15.0
    # Bu orandan fazla "indirim" büyük ihtimalle veri hatasıdır; yine de bildirilir ama uyarı eklenir.
    SUSPICIOUS_DISCOUNT_PCT: float = 45.0

    PERPLEXITY_API_KEY: str = ""
    PERPLEXITY_MODEL: str = "sonar"
    PRICE_CACHE_DAYS: int = 7

    # --- Hasar filtresi (izin verilen en fazla) ---------------------------
    MAX_DEGISEN: int = 2
    MAX_BOYALI: int = 4

    # --- Döngü / dayanıklılık ----------------------------------------------
    SCRAPE_INTERVAL_MINUTES: int = 30
    CYCLE_TIMEOUT_MINUTES: int = 40
    MAX_LISTINGS_PER_CYCLE: int = 40
    JITTER_MIN_SECONDS: float = 3.0
    JITTER_MAX_SECONDS: float = 7.0
    HEADLESS: bool = False
    # Virgülle ayrılmış proxy listesi (http://kullanici:sifre@host:port). Boşsa doğrudan bağlanır.
    PROXY_URL: str = ""

    # --- Sağlık izleme ------------------------------------------------------
    HEARTBEAT_HOURS: int = 24
    BLOCKED_CYCLES_ALERT: int = 2

    # --- Galerici profilleri ------------------------------------------------
    DEALERS_JSON: str = ""
    DEALERS_FILE: str = "dealers.json"

    DB_PATH: str = "arbitrage_db.sqlite"

    LOG_LEVEL: str = "INFO"

    @property
    def database_url(self) -> str:
        # Railway kalıcı volume için DB_PATH ile geçersiz kılınabilir (örn. /data/arbitrage_db.sqlite)
        return f"sqlite+aiosqlite:///{self.DB_PATH}"

    @property
    def admin_chat(self) -> str:
        return self.ADMIN_CHAT_ID or self.TELEGRAM_CHAT_ID

    @property
    def target_urls(self) -> list[str]:
        return [u.strip() for u in self.TARGET_URL.split(",") if u.strip()]

    @property
    def proxy_urls(self) -> list[str]:
        return [u.strip() for u in self.PROXY_URL.split(",") if u.strip()]

    @property
    def modes(self) -> set[str]:
        raw = self.BOT_MODE.strip().lower()
        if raw == "both":
            return {"market", "dealer"}
        if raw in ("market", "dealer"):
            return {raw}
        raise ValueError(f"BOT_MODE must be market, dealer or both (got {self.BOT_MODE!r})")

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }


settings = Settings()
