# DURUM — arbitrage_engine

Son güncelleme: 2026-10-25 00:50 (TR)

## Canlıdaki durum
- Railway (`truthful-trust` / `galeri-bot`): canlı sürüm `7c2f278`. **Yerelde commit'li ama GÖNDERİLMEMİŞ: `b0c57ef` + `8446625` + Perplexity yedeği commit'i** (temizlik + yeni özellikler + tahmin yedeği, aşağıda). Railway'de `PERPLEXITY_API_KEY` duruyor (temizlik betiği artık silmez).
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

## Perplexity tahmin yedeği (25.10 00:45, yerelde, 100 test geçiyor; kullanıcı kararı)
- `src/estimate.py` (`PriceService`) + `estimate_cache` tablosu. Yalnız arabam'da <10 aynı donanım emsali varsa ve site engelli değilse sorulur (`Benchmark.stats`); fiyat modeli / hedef fiyat / acil başlık varsa SORULMAZ (para harcanmasın).
- Karar penceresi: ilan tahminden **%25–%40** ucuz (`ESTIMATE_MIN/MAX_DISCOUNT_PCT`; üst sınır benim eklediğim güvenlik: aşırı ucuz = tahmin hatası). Galericide ayrıca TL tabanı. Genel modda tahminli adayın puanı ×0,75. Mesajda 🔮 "tahmin, kesin değil" etiketi; alert `reason`'ında "tahmin" geçer → günlük özette tahminli mesajların 👍/👎'ı ayrı sayılır.
- 7 gün önbellek (bilinmeyen/null cevap 1 gün), turda en çok 15 sorgu, 3 ardışık hatada durur; `auth`/`quota` hatasında yöneticiye uyarı. Anahtar boşsa kapalı (kapatmak = `PERPLEXITY_API_KEY` sil).
- Ölçüm (25.10): 13 yaygın araçta cevap verince arabam ortancasına yakın (tipik hata %5–8, hiçbiri %17+ yüksek değil) ama sorguların ~%25–55'ine cevap veriyor; nadir araçlarda 14/23 cevap, yuvarlak sayılar (tahmin gibi), doğruluğu ölçülemedi. İlan satırı vermiyor (liste isteyince boş döner). Canlı API'de üretim istemiyle 4 denemede 1 cevap geldi.
- Maliyet: ~0,4 sent/sorgu (sonar-pro + yüksek bağlam). Test için ~0,5 USD harcandı.

## Sıradaki işler
- **B1** 25.10 12:00: engel kalktı mı bak (Railway log). Kalkmadıysa yeni IP/yedek proxy.
- **B2** Engel kalkınca: `git push` (3 commit) → dağıtımı izle → düğme/`/start`/telefon/tahmin mesajı canlı doğrula → `railway_degisken_temizle.py --sil` (önce çıktıyı kontrol et; `PERPLEXITY_API_KEY` kalan listesinde).
- **B3** Pazartesi/salı Adana Oto Galericiler Odası Başkanı sunumu: tek dosyalık koyu temalı HTML (`impeccable` skill + hafızadaki sunum stili), gerçek mesaj örnekleri, fiyat önerisi, 2 hafta ücretsiz pilot.
- **B4** Gelen mesajlar 👍/👎 ile birikince kural/eşik ayarı (otomatik eğitim YOK; ölçüm var, ayarı elle yaparız).
- Ertelenen/gereksiz (kullanıcı kararı): fiyat düşüşü takibi, "satışta mı" kontrolü, tekrar ilan eşleştirme, tramer yoksa yapılacak bir şey yok, arabam şartları riski kabul edildi.
- Açık not: fiyat modeli yalnız 5 model için; diğer modellerde aynı donanımdan ≥10 emsal yoksa artık Perplexity tahmini sorulur (cevap gelirse ve %25–40 farkta mesaj). Cevap gelmezse mesaj çıkmaz. Çoklu galericide tek proxy ≤~10 farklı arama sayfası (yaklaşık 5 galerici).
