# DURUM — arbitrage_engine

Son güncelleme: 2026-10-25 11:20 (TR)

## Canlıdaki durum
- **25.10 11:08 (TR): Railway canlı sürüm `9ea3214` (temizlik + düğmeler + Perplexity yedeği + proxy eşitleme), YENİ proxy `131.222.224.26:10225` (çıkış IP 153.56.216.133).** Engel çözüldü: ilk tur temiz (270 bağlantı, 20 ilan incelendi, 0 hata, 1 mesaj gitti, Perplexity canlıda sorgu attı). Eski proxy (10171) bırakıldı; `Proxy` tablosundaki eski kayıt `seed` ile kapanıyor. Bu satırın altındaki "ENGEL" ve "Karar" maddeleri TARİHÎ.
- Doğrulanmamış (canlıda henüz görülmedi): mesajdaki 👍/👎 görünümü ve oy kaydı, `/start`, telefon satırı (galerici mesajı), 22:00 galerici özeti, tahminli mesajın (🔮) gerçek Telegram görünümü. Railway'de fazla değişkenler hâlâ duruyor: `railway_degisken_temizle.py` (önce çıktıyı kontrol et, sonra `--sil`; `PERPLEXITY_API_KEY` kalanlar listesinde).
- Bilinen açıklar (24-25.10 taraması): (1) engelliyken galerici 22:00 özeti "yeni ilan çıkmadı" diyor (yanlış güvence), (2) galerici başına günlük mesaj üst sınırı yok, (3) tur 20 dk → gecikme, (4) fiyat bandı yalnız VW 5 model, (5) isabet oranı ölçülmedi, (6) disk yedeği yok.
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

## Ölçümler 25.10 (öğleden sonra)
- **Son canlı mesajın (Passat 1.6 TDI Elegance 2019, 195 bin km, 1.595.000 TL) kıyası yeniden üretildi:** aynı donanım 18 emsal, düzeltilmiş ortanca 2.082.632 TL, ilan hepsinin en ucuzu; ama ilan 2 değişen (motor kaputu, sol ön çamurluk) + 1 boyalı, emsaller çoğunlukla temiz. Bot hasar için yalnız 9,5 puan düşüyor (4/değişen, 1,5/boyalı — TAHMİN, ölçülmedi). Kullanıcı piyasada ~1,7 milyon gördü → büyük ihtimalle düşük donanım ya da hasarlı. **Açık: hasar payı gerçek veriyle ölçülüp düzeltilmeli** (öneri: ~100 ilan detayı yerelde, ev IP'sinden yavaş, canlı proxy'ye dokunma). Mesajdaki sayılar da karışık: başlık %13,9 (hasar düşülmüş), "Fark" satırı %23,4 (ham).
- **Perplexity hasarı fiyata yansıtmıyor (6 araç, 4 senaryo, ~40 sorgu):** cevap oranı hasarsızda 8/12, 2 değişende 5/12, ağır hasarda 2/6; tepki tutarsız (Astra'da 1 boyalı +%21, Leon'da 2 değişen %0, Megane 2 değişen −%19, ağır hasar −%28…−%41). Asıl Passat için 2 denemeden biri "yok", diğeri 1.900.000. Karar (kullanıcının kuralıyla): Perplexity YEDEK kalır, çift katman doğrulayıcı olmaz. İsteğe bağlı: bilgi amaçlı "ikinci görüş" satırı.

## Hasar payı ölçümü (25.10 öğleden sonra, ev IP'sinden, 139 ilan, 10 aynı-donanım grubu)
Grup sabit etkili log-fiyat regresyonu (araç ilan fiyatı ~ yıl + km + değişen sayısı + boyalı sayısı). Ham veri: scratchpad `damage/data.jsonl`, betikler `damage_study.py` / `damage_analyze.py`.
- her değişen parça **−%6,2** (%95: −7,7…−4,7) — bot 4 puan düşüyor (az); her boyalı parça **−%1,4** (−2,0…−0,8) — bot 1,5 (doğru); +1 yıl **+%5,2** (bot 3,5; 23.10 ölçümü 3,6 — çelişik); +10 bin km **−%1,9** (bot 1,5; 23.10 ölçümü 1,5).
- Kalıntı sapma ≈ %7: tek bir ilanın adil fiyatı ±%7 oynar → ≥%8 yolu gürültü düzeyinde.
- İlanların %71'inde boya/değişen var (ortalama 0,46 değişen, 2,14 boyalı); yani emsal ortancası "temiz araç" değil. Passat Elegance emsalleri bu grupta temizdi (14 ilanın 2'sinde değişen).
- Son canlı mesaj (Passat Elegance 2019, 195 bin km, 2 değişen+1 boyalı, 1.595.000): regresyonla adil fiyat ≈ 1.838.000 → ilan ≈ %13 altında; en yakın hasarlı emsal (230 bin km, 2/1, 1.635.000) km düzeltmesiyle ≈ 1.74 milyon → ≈ %8 altında. Bot net %13,9 demişti: makul ama "Fark %23,4 / 487 bin TL" satırı abartılı. 
- Öneri (henüz uygulanmadı): `PENALTY_PER_DEGISEN` 4 → 6; mesajda tek tutarlı sayı (hasar payı düşülmüş referans ve fark); yıl/km katsayısına şimdilik dokunma.

## Sıradaki işler
- ~~B1 engel kontrolü~~ ve ~~B2 push~~: YAPILDI 25.10 11:08 (yeni proxy + 4 commit canlıda).
- **B2b** Canlıda doğrula: 👍/👎, `/start`, tahmin mesajı, 22:00 özeti; sonra `railway_degisken_temizle.py` (dry-run → `--sil`).
- **B2c** İstenirse: bilinen iki açığı kapat (engelde galerici özeti, günlük galerici sınırı).
- **B3** Pazartesi/salı Adana Oto Galericiler Odası Başkanı sunumu: tek dosyalık koyu temalı HTML (`impeccable` skill + hafızadaki sunum stili), gerçek mesaj örnekleri, fiyat önerisi, 2 hafta ücretsiz pilot.
- **B4** Gelen mesajlar 👍/👎 ile birikince kural/eşik ayarı (otomatik eğitim YOK; ölçüm var, ayarı elle yaparız).
- Ertelenen/gereksiz (kullanıcı kararı): fiyat düşüşü takibi, "satışta mı" kontrolü, tekrar ilan eşleştirme, tramer yoksa yapılacak bir şey yok, arabam şartları riski kabul edildi.
- Açık not: fiyat modeli yalnız 5 model için; diğer modellerde aynı donanımdan ≥10 emsal yoksa artık Perplexity tahmini sorulur (cevap gelirse ve %25–40 farkta mesaj). Cevap gelmezse mesaj çıkmaz. Çoklu galericide tek proxy ≤~10 farklı arama sayfası (yaklaşık 5 galerici).
