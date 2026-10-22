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

    # --- Karşılaştırma modu: Perplexity'siz, arabam'daki benzer ilanlarla kıyaslar -------------------
    COMPARE_MIN_DISCOUNT_PCT: float = 15.0   # benzerlerinin ortasından en az bu kadar ucuz
    COMPARE_MAX_ITEMS: int = 1               # her gönderimde en iyi kaç ilan
    COMPARE_RELEASE_MINUTES: int = 60        # iki gönderim arası en az süre (havuzdan en iyisi seçilir)
    COMPARE_FRESH_HOURS: float = 3.0         # bundan eski aday gönderilmez
    COMPARE_DAILY_MAX: int = 15              # günde en çok kaç karşılaştırma mesajı (Türkiye saatiyle gün)
    COMPARE_MIN_COMPARABLES: int = 5         # karşılaştırma için en az benzer ilan
    COMPARE_YEAR_TOLERANCE: int = 1
    COMPARE_KM_TOLERANCE_PCT: float = 40.0
    BENCH_TTL_HOURS: float = 6.0             # aynı sayfa bu süre yeniden açılmaz
    BENCH_MAX_FETCH_PER_CYCLE: int = 8       # bir turda açılacak en çok karşılaştırma sayfası

    # --- Hasar filtresi (izin verilen en fazla) ---------------------------
    MAX_DEGISEN: int = 2
    MAX_BOYALI: int = 4

    # --- Döngü / dayanıklılık ----------------------------------------------
    SCRAPE_INTERVAL_MINUTES: int = 30
    CYCLE_TIMEOUT_MINUTES: int = 40
    MAX_LISTINGS_PER_CYCLE: int = 40
    PAGES_PER_TARGET: int = 2          # liste sayfasının kaç sayfası taransın (sayfa başı ~20 ilan)
    JITTER_MIN_SECONDS: float = 3.0
    JITTER_MAX_SECONDS: float = 7.0
    HEADLESS: bool = False
    # "chrome" = gerçek Google Chrome (Cloudflare'i geçmek için paketli Chromium'dan daha iyi). Boş = paketli Chromium.
    BROWSER_CHANNEL: str = ""
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
        """BOT_MODE: market, dealer, compare; virgülle birleştirilebilir. both=market+dealer, all=hepsi."""
        found: set[str] = set()
        for token in self.BOT_MODE.lower().replace("+", ",").split(","):
            token = token.strip()
            if token == "both":
                found |= {"market", "dealer"}
            elif token == "all":
                found |= {"market", "dealer", "compare"}
            elif token in ("market", "dealer", "compare"):
                found.add(token)
            elif token:
                raise ValueError(f"BOT_MODE geçersiz: {token!r} (market, dealer, compare, both, all)")
        if not found:
            raise ValueError("BOT_MODE boş")
        return found

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }


settings = Settings()
