# Arbitrage Engine

İkinci el araç ilanlarını tarayan, her ilanı aynı marka/model/yıldaki araçların piyasa ortalamasıyla karşılaştıran ve ortalamanın belirli bir oranın altında fiyatlanan ilanları Telegram'a bildiren bot.

**Akış:** Scraper (Playwright + proxy rotasyonu) → Analyzer (PostgreSQL) → Telegram bildirimi

## Gereksinimler

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Docker Compose v2 dahil)
- Git

## Kurulum (sıfırdan)

### 1. Projeyi klonlayın

```bash
git clone <repo-url> arbitrage_engine
cd arbitrage_engine
```

### 2. Ortam değişkenlerini hazırlayın

```bash
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

`.env` dosyasında en azından şunları düzenleyin:

| Değişken | Açıklama |
|---|---|
| `POSTGRES_PASSWORD` | Veritabanı şifresi. URL içinde kullanıldığı için `@ : / ? #` gibi karakterler kullanmayın. |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) üzerinden alınan token. |
| `TELEGRAM_CHAT_ID` | Bildirimlerin gideceği sohbet ID'si. |
| `SCRAPE_TARGETS` | Taranacak arama sonuç sayfaları (tek satırda JSON listesi). |
| `PROXY_API_URL` | *(İsteğe bağlı)* Proxy listesi dönen uç nokta. Boş kalırsa doğrudan bağlanılır. |

> Docker içinde `DATABASE_URL` ve `REDIS_URL` değerlerini `docker-compose.yml` otomatik olarak servis adlarına (`postgres`, `redis`) göre ayarlar. `.env` içindeki bu iki değer yalnızca botu Docker olmadan çalıştırırken kullanılır.

### 3. Başlatın

```bash
docker compose up -d --build
```

İlk derleme Chromium'u indirdiği için birkaç dakika sürebilir. Bot, PostgreSQL ve Redis hazır olduğunda başlar, tabloları kendisi oluşturur ve `SCRAPE_INTERVAL_SECONDS` aralıklarla taramaya devam eder.

### 4. Kontrol edin

```bash
docker compose ps              # üç servis de "running" / "healthy" olmalı
docker compose logs -f bot     # botun canlı logları
```

## Sık kullanılan komutlar

```bash
# Tek bir tarama turu çalıştırıp çık (test için)
docker compose run --rm bot python main.py --once

# Belirli bir URL'yi tek seferlik tara
docker compose run --rm bot python main.py --once --url "https://www.sahibinden.com/..."

# Veritabanına bağlan
docker compose exec postgres psql -U arbitrage -d arbitrage

# Kod değişikliğinden sonra yeniden derle
docker compose up -d --build bot

# Durdur (veriler korunur)
docker compose down

# Durdur ve TÜM verileri sil (veritabanı dahil)
docker compose down -v
```

Proxy'leri elle eklemek için:

```sql
INSERT INTO proxies (url) VALUES ('http://kullanici:sifre@1.2.3.4:8080');
```

## Notlar

- `POSTGRES_*` değerleri yalnızca veritabanı ilk oluşturulurken uygulanır. Sonradan şifreyi değiştirirseniz `docker compose down -v` ile volume'u silmeniz gerekir (veriler de silinir).
- Detaylı dosya logları `app_logs` volume'unda (`/app/logs/app.log`) tutulur.
- Piyasa ortalaması, aynı marka/model/yılda en az `MIN_SAMPLE_SIZE` ilan birikene kadar hesaplanmaz. Bu yüzden ilk turlarda fırsat çıkmaması normaldir.
- Sitenin HTML yapısı değişirse CSS seçicilerini `scraper/engine.py` içindeki `SelectorConfig` sınıfından güncelleyin.
- Hedef sitenin kullanım koşullarına uymak kullanıcının sorumluluğundadır.

## Docker olmadan çalıştırma

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python main.py --once
```

Bu durumda PostgreSQL'in lokalde çalışıyor olması ve `.env` içindeki `DATABASE_URL`'in ona işaret etmesi gerekir.
