"""Ayarlar. Varsayılanlar CANLI değerlerdir: Railway'de yalnız gizli bilgiler ve ortama özel olanlar tanımlı olmalı
(TELEGRAM_*, PROXY_URL, DEALERS_JSON, TARGET_URL, DB_PATH, HEADLESS, BROWSER_CHANNEL, PERPLEXITY_API_KEY).
Başka bir değişken kodu ezer."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str = ""
    # Yönetici sohbeti: sağlık uyarıları ve chat_id'si olmayan galerici mesajları. ADMIN_CHAT_ID doluysa uyarılar oraya gider.
    TELEGRAM_CHAT_ID: str = ""
    ADMIN_CHAT_ID: str = ""

    # Genel mod için taranacak liste sayfası(ları). Birden fazlaysa virgülle ayır.
    TARGET_URL: str = ""

    # compare : genel mod, aynı donanımdaki güncel ilanlara göre ucuz olanlardan seçilen en iyi ilan
    # dealer  : galerici profilleri (DEALERS_JSON / dealers.json)
    BOT_MODE: str = "compare,dealer"

    # --- Genel mod (compare) -------------------------------------------------
    COMPARE_MIN_DISCOUNT_PCT: float = 15.0   # emsal ortancasından en az bu kadar ucuz
    COMPARE_MAX_ITEMS: int = 1               # her gönderimde en iyi kaç ilan
    COMPARE_RELEASE_MINUTES: int = 90        # iki gönderim arası en az süre
    COMPARE_FRESH_HOURS: float = 3.0         # bundan eski aday gönderilmez
    COMPARE_DAILY_MAX: int = 10              # günde en çok mesaj (Türkiye saatiyle gün)
    COMPARE_ACTIVE_START: int = 8            # gönderim saatleri (Türkiye saati), [başlangıç, bitiş)
    COMPARE_ACTIVE_END: int = 23
    COMPARE_MIN_COMPARABLES: int = 10        # en az benzer ilan (aynı donanım, yıl ±1, km ±%30)
    COMPARE_LEVELS: int = 2                  # 1=aynı donanım, 2=+aynı motor, 3=+tüm model
    COMPARE_MAX_DISCOUNT_PCT: float = 30.0   # bundan büyük fark aday olmaz (hata/dolandırıcılık şüphesi)
    COMPARE_MIN_PROFIT_TL: int = 150000      # %15 altında bile: bu kadar TL ucuzsa ve alt yüzdeyi geçiyorsa aday (0 = kapalı)
    COMPARE_MIN_PROFIT_PCT: float = 8.0
    COMPARE_MAX_KM: int = 200000             # bunun üstü km aday olmaz (0 = sınır yok)
    COMPARE_MAX_AGE_YEARS: int = 12          # bundan eski model aday olmaz (0 = sınır yok)
    COMPARE_YEAR_TOLERANCE: int = 1
    COMPARE_KM_TOLERANCE_PCT: float = 30.0
    BENCH_TTL_HOURS: float = 6.0             # aynı emsal sayfası bu süre yeniden açılmaz
    BENCH_MAX_FETCH_PER_CYCLE: int = 25      # bir turda açılacak en çok emsal sayfası

    # --- İnternet taraması tahmini (Perplexity) - YEDEK -------------------------
    # Yalnız arabam'da yeterli (COMPARE_MIN_COMPARABLES) aynı donanım emsali yoksa sorulur. Anahtar boşsa kapalı.
    PERPLEXITY_API_KEY: str = ""
    PERPLEXITY_MODEL: str = "sonar-pro"
    PERPLEXITY_CACHE_DAYS: int = 7           # aynı araç tipi/yıl/km dilimi için tahmin bu süre yeniden sorulmaz
    PERPLEXITY_MAX_CALLS_PER_CYCLE: int = 15 # bir turda en çok sorgu (maliyet sınırı)
    ESTIMATE_MIN_DISCOUNT_PCT: float = 25.0  # ilan tahminden en az bu kadar ucuz olmalı
    ESTIMATE_MAX_DISCOUNT_PCT: float = 40.0  # bundan fazlası çoğunlukla tahmin hatası: aday olmaz

    # --- Hasar filtresi (genel mod için izin verilen en fazla) ---------------
    MAX_DEGISEN: int = 2
    MAX_BOYALI: int = 4

    # --- Galerici modu ---------------------------------------------------------
    DEALERS_JSON: str = ""
    DEALERS_FILE: str = "dealers.json"
    DEALER_ACTIVE_START: int = 8             # galerici mesajları Türkiye saatiyle bu saatler arasında gider
    DEALER_ACTIVE_END: int = 23
    DEALER_PAGES: int = 2                    # galerici arama sayfalarının kaç sayfası taransın
    DEALER_SUMMARY_HOUR: int = 22            # günlük kısa özet bu saatte (Türkiye saati) galericilere gider

    # --- Döngü / dayanıklılık --------------------------------------------------
    SCRAPE_INTERVAL_MINUTES: int = 20
    CYCLE_TIMEOUT_MINUTES: int = 40
    MAX_LISTINGS_PER_CYCLE: int = 20
    PAGES_PER_TARGET: int = 2                # genel liste sayfasının kaç sayfası taransın (sayfa başı ~20 ilan)
    JITTER_MIN_SECONDS: float = 3.0
    JITTER_MAX_SECONDS: float = 7.0
    RESCAN_LISTINGS: str = ""                # örn. "Passat,Golf": bu sözcükleri içeren bilinen ilanları bir kez unut (kural değişince)
    HEADLESS: bool = False                   # Railway: false (xvfb), yerelde true
    BROWSER_CHANNEL: str = ""                # Railway: chrome (Cloudflare için gerçek Chrome); boş = paketli Chromium
    PROXY_URL: str = ""                      # virgülle ayrılmış http://kullanici:sifre@host:port; boşsa doğrudan

    # --- Sağlık izleme ---------------------------------------------------------
    HEARTBEAT_HOURS: int = 24
    BLOCKED_CYCLES_ALERT: int = 2

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
        """BOT_MODE: compare, dealer; virgülle birleştirilebilir."""
        found: set[str] = set()
        for token in self.BOT_MODE.lower().replace("+", ",").split(","):
            token = token.strip()
            if token in ("dealer", "compare"):
                found.add(token)
            elif token:
                raise ValueError(f"BOT_MODE geçersiz: {token!r} (compare, dealer)")
        if not found:
            raise ValueError("BOT_MODE boş")
        return found

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }


settings = Settings()
