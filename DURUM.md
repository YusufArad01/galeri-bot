# DURUM — arbitrage_engine

Son güncelleme: 2026-10-25 00:20 (TR)

## Canlıdaki durum
- Railway (`truthful-trust` / `galeri-bot`): canlı sürüm `7c2f278`. **Yerelde commit'li ama GÖNDERİLMEMİŞ: `b0c57ef`** (temizlik + yeni özellikler, aşağıda).
- `BOT_MODE=compare,dealer`. Tek ProxyTurk proxy (IP 153.56.219.29, canlı).
- **ENGEL:** 24.10 14:46'dan beri Railway→arabam Cloudflare 403 (IP işaretlendi; sebep: benim artırdığım istek yükü ~250/saat + sık yeniden başlatma). Canlıda: engelde sayfa isteme + 15/30/60/120 dk geri çekilme. Her deploy sayacı sıfırlar.
- **Karar (kullanıcı): 25.10 öğlen 12:00'ye kadar bekle.** Kalkmadıysa ProxyTurk'ten yeni IP; yedek proxy kodu `proxy-rotasyonu` dalında (birleşmemiş, güncel koda uyarlanmalı).
- Genel mod: aynı donanım emsali ≥10 ilan, ortancadan ≥%15 ya da (≥150k TL ve ≥%8), ≤%30, alt çeyrek şartı, ≤12 yaş/≤200k km, günde ≤10, 90 dk'da 1, 08–23 TR.
- Galerici: demo `vw-uzmani` (9 kural; Passat/Variant/Golf/Polo/Tiguan fiyat modelli), referanstan ≥100.000 TL ucuz + alt çeyrek + %3 alt sınır; acil/kelepir başlığı fiyatsız gider; 08–23 TR; chat_id boş → yöneticiye.

## `b0c57ef` içinde (yerelde hazır, 88 test geçiyor; engel kalkınca tek seferde gönder)
- **Silinen:** eski Postgres mimarisi (kök `analyzer/ core/ models/ notifier/ proxy/ scraper/`, docker-compose), market modu + Perplexity, vava motoru, Celery, eski tablolar (açılışta DROP), gereksiz bağımlılıklar.
- **Ayar:** `src/config.py` varsayılanları = canlı değerler (interval 20, tur başı 20 ilan, DEALER_PAGES 2, emsal sayfası 25…). Gönderdikten SONRA `python railway_degisken_temizle.py --sil` ile Railway'deki fazla değişkenler silinir (önce çıktıyı kontrol et; kalan: TELEGRAM_*, PROXY_URL, DEALERS_JSON, TARGET_URL, DB_PATH, HEADLESS, BROWSER_CHANNEL).
- **Veri temizliği** (`src/maintenance.py`): günde bir; emsal önbelleği 2 gün, ilan 90 gün, mesaj 180 gün; hata görüntüleri en çok 20 dosya.
- **Telegram:** `src/feedback.py` botu dinler: 👍/👎 düğmeleri (`feedback` tablosu), `/start` (kişiye numarasını söyler, yöneticiye bildirir). Reddedilen mesaj (403/400) FAILED olur + yöneticiye uyarı. SQLite bağlantı zaman aşımı 30 sn.
- **Mesaj:** ilan sayfasında telefon varsa 📞 satırı (arabam göstermezse yalnız link). Güçlü fırsat (fark ≥150.000 TL) `★` ile işaretlenir.
- **Özetler:** galericiye 22:00'de "bugün size N ilan gönderdim, K tanesi güçlü" (0 ise "uygun ilan çıkmadı, takipteyim"); yönetici özeti sadeleşti (Perplexity satırı yok, 👍/👎 sayısı var). Ekspertiz okunamıyorsa "sayfa yapısı değişmiş" uyarısı.
- `chat_id_bul.py` kaldırıldı (yerine `/start`).
- **Canlıda hiç denenmedi:** 👍/👎 düğmeleri, `/start`, telefon okuma, 22:00 özeti, veri temizliği (yalnız birim test + eski şemalı veritabanında açılış denemesi yapıldı).

## Sıradaki işler
- **B1** 25.10 12:00: engel kalktı mı bak (Railway log). Kalkmadıysa yeni IP/yedek proxy.
- **B2** Engel kalkınca: `git push` (b0c57ef) → dağıtımı izle → düğme/`/start`/telefon canlı doğrula → `railway_degisken_temizle.py --sil`.
- **B3** Pazartesi/salı Adana Oto Galericiler Odası Başkanı sunumu: tek dosyalık koyu temalı HTML (`impeccable` skill + hafızadaki sunum stili), gerçek mesaj örnekleri, fiyat önerisi, 2 hafta ücretsiz pilot.
- **B4** Gelen mesajlar 👍/👎 ile birikince kural/eşik ayarı (otomatik eğitim YOK; ölçüm var, ayarı elle yaparız).
- Ertelenen/gereksiz (kullanıcı kararı): fiyat düşüşü takibi, "satışta mı" kontrolü, tekrar ilan eşleştirme, tramer yoksa yapılacak bir şey yok, arabam şartları riski kabul edildi.
- Açık not: fiyat modeli yalnız 5 model için; diğer modellerde aynı donanımdan ≥10 emsal yoksa mesaj sessizce çıkmaz (istenirse `COMPARE_MIN_COMPARABLES` düşürülür). Çoklu galericide tek proxy ≤~10 farklı arama sayfası (yaklaşık 5 galerici).
