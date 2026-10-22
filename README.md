# Arbitrage Engine

İkinci el araç ilanlarını (arabam.com, isteğe bağlı VavaCars) tarar, fırsatları Telegram'a bildirir.
Railway'de `python main.py` ile çalışır; veri SQLite'tadır (`DB_PATH`).

> Kök dizindeki `analyzer/`, `core/`, `models/`, `notifier/`, `scraper/`, `docker-compose.yml` ve Celery
> dosyaları eski (PostgreSQL) mimariden kalmadır; **çalışan kod `main.py` + `src/`'dir.**

## İki çalışma modu (`BOT_MODE`)

| Mod | Ne yapar |
|---|---|
| `market` (varsayılan) — **Sürüm 1** | İlanı okur, Perplexity'den aynı araç için piyasa ortalamasını çeker, fiyat ortalamanın **%15** (ayarlı) altındaysa `TELEGRAM_CHAT_ID`'ye bildirir. |
| `dealer` — **Sürüm 2** | Galerici profillerine göre çalışır. Her galericinin uzman olduğu marka/model ve hedef fiyatı vardır; hedef fiyatın altındaki ilan o galericinin kendi sohbetine gider. İsteğe bağlı `market_check` ile piyasa ortalamasının %X altı da yakalanır. |
| `compare` — **Sürüm 3** | Perplexity kullanmaz. Her turda yeni ilanlar filtrelerden (hasar, yaş/fiyat) geçirilir; kalanlar arabam'daki **aynı donanım → motor → model** sayfalarındaki güncel ilanların (benzer yıl/km) fiyat ortasıyla kıyaslanır. Turun en avantajlı ilanı (`COMPARE_MAX_ITEMS`, varsayılan 1) benzerlerinden en az %15 ucuzsa gönderilir; değilse mesaj yok. Karşılaştırma sayfaları 6 saat önbelleğe alınır, turda en çok 8 yeni sayfa açılır. |
| `both` / `all` | `both` = market+dealer, `all` = üçü birlikte. Virgülle de birleştirilir: `market,compare`. |

İki sürümü ayrı Railway servisleri olarak çalıştırmak için aynı depoyu iki servise bağlayıp yalnızca
`BOT_MODE` ve `DB_PATH` değerlerini farklı verin.

### Galerici profili
`dealers.example.json` dosyasını `dealers.json` olarak kopyalayın (veya içeriği `DEALERS_JSON` ortam
değişkenine koyun — Railway için önerilir). Dosya her döngüde yeniden okunur; bozuksa bot son geçerli
ayarla devam eder ve yöneticiye uyarı gönderir.

| Alan | Açıklama |
|---|---|
| `id`, `name`, `chat_id` | Galerici kimliği ve Telegram sohbet ID'si. Galerici botu bir kez `/start` etmeli. `chat_id` boşsa mesaj yönetici sohbetine gider. |
| `market_check` / `market_discount_pct` | Açıksa kurala uyan ilanlar için Perplexity piyasa ortalaması da kontrol edilir (varsayılan %15). |
| `allow_damaged` | `true` ise hasar/boya filtresine takılan ilanlar da gelir (mesajda uyarıyla). |
| `rules[].brand` / `model` | Marka zorunlu, model isteğe bağlı. Model, ilan başlığı/seri/donanımındaki kelimelerle eşleşir (`"C 200"`, `"Megane 1.5"`). |
| `rules[].year_min/year_max/km_max` | İsteğe bağlı sınırlar. |
| `rules[].max_price` | Hedef fiyat (TL). Bunun altı/eşiti bildirilir. `market_check` açıksa boş bırakılabilir. |
| `rules[].search_url` | İsteğe bağlı: o marka/model için arabam.com arama sayfası. Verilirse bot o sayfayı da tarar (daha isabetli). |

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | – | Zorunlu. |
| `ADMIN_CHAT_ID` | `TELEGRAM_CHAT_ID` | Sağlık/uyarı mesajları. |
| `TARGET_URL` | – | Taranacak liste sayfası (virgülle birden fazla). |
| `BOT_MODE` | `market` | `market` / `dealer` / `both`. |
| `PERPLEXITY_API_KEY` | – | Piyasa fiyatı için zorunlu. |
| `DISCOUNT_THRESHOLD_PCT` | `15` | Piyasa altı eşik (%). |
| `SUSPICIOUS_DISCOUNT_PCT` | `45` | Bu kadar "indirim" veri hatası şüphesi olarak işaretlenir. |
| `MAX_DEGISEN` / `MAX_BOYALI` | `2` / `4` | İzin verilen en fazla değişen / boyalı parça. |
| `SCRAPE_INTERVAL_MINUTES` | `30` | Döngü aralığı. |
| `MAX_LISTINGS_PER_CYCLE` | `40` | Döngüde incelenecek en çok yeni ilan. |
| `PAGES_PER_TARGET` | `2` | Liste sayfasının kaç sayfası taransın (sayfa başı ~20 ilan). |
| `COMPARE_MIN_DISCOUNT_PCT` / `COMPARE_MAX_ITEMS` | `15` / `1` | Karşılaştırma modunda en az ucuzluk (%) ve turda gönderilecek en çok ilan. |
| `COMPARE_MIN_COMPARABLES` / `COMPARE_YEAR_TOLERANCE` / `COMPARE_KM_TOLERANCE_PCT` | `5` / `1` / `40` | En az benzer ilan, yıl farkı, km farkı (%). |
| `BENCH_TTL_HOURS` / `BENCH_MAX_FETCH_PER_CYCLE` | `6` / `8` | Karşılaştırma sayfası önbellek süresi ve turda açılacak en çok yeni sayfa. |
| `CYCLE_TIMEOUT_MINUTES` | `40` | Takılan döngüyü iptal eder. |
| `PROXY_URL` | – | Virgülle proxy listesi. Railway veri merkezi IP'leri engellenirse konut/mobil proxy burada. |
| `PRICE_CACHE_DAYS` | `7` | Aynı araç için Perplexity sonucu önbellek süresi. |
| `HEARTBEAT_HOURS` | `24` | Günlük özet mesajı aralığı. |
| `BLOCKED_CYCLES_ALERT` | `2` | Kaç ardışık boş/engelli döngüden sonra uyarı verilsin. |
| `DB_PATH` | `arbitrage_db.sqlite` | Railway'de kalıcı volume için örn. `/data/arbitrage_db.sqlite`. |
| `DEALERS_JSON`, `DEALERS_FILE` | – / `dealers.json` | Galerici profili. |

## "Mesaj gelmiyor" nasıl ayırt edilir?

Bot artık sessizliğin sebebini kendisi bildirir (yönetici sohbetine):
- açılışta "🟢 başladı" mesajı, her gün özet ("📊 Günlük özet": kaç ilan görüldü/elendi/bildirildi);
- siteden art arda ilan alınamıyorsa "🚧 engel/captcha" uyarısı, düzelince "✅";
- Perplexity anahtarı yok/geçersiz/bakiye bitti/limit → "💸 piyasa servisi sorunlu";
- takılan döngü, bozuk galerici dosyası, beklenmeyen hata uyarıları.

Günlük özet geliyor ve hata uyarısı yoksa o sürede eşiği geçen ilan çıkmamıştır.
Telegram'a gidemeyen bildirimler `alerts` tablosunda bekler ve sonraki döngüde yeniden denenir.

## Test

```bash
python -m unittest discover -s tests -t .
```

Testler canlı site/Telegram/Perplexity'ye bağlanmaz (sahte HTTP taşıyıcıları kullanır).

## Yerelde çalıştırma

```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt && playwright install chromium
python main.py
```
Yerelde `.env` dosyası okunur. Aynı bot token'ı hem Railway'de hem yerelde çalışırsa Telegram 409 verir; Railway'i durdurun.
