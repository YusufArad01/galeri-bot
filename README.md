# Arbitrage Engine

İkinci el araç ilanlarını (arabam.com) tarar, fırsatları Telegram'a bildirir. Railway'de `python main.py` ile çalışır;
veri SQLite'tadır (`DB_PATH`). Çalışan kod: `main.py` + `src/`.

## İki çalışma modu (`BOT_MODE`, varsayılan `compare,dealer`)

| Mod | Ne yapar |
|---|---|
| `compare` — genel | Yeni ilanlar hasar/yaş/km filtresinden geçer, **aynı donanımdaki** güncel arabam ilanlarının (yıl ±1, km ±%30, en az 10) ortancasıyla kıyaslanır. İlan, emsallerin en ucuz çeyreğinde ve ortancadan %15 (ya da ≥150.000 TL ve ≥%8) ucuzsa aday olur. 90 dk'da bir en iyi aday, günde en çok 10, 08–23 arası, `TELEGRAM_CHAT_ID`'ye gider. |
| `dealer` — galerici | Her galericinin kuralları (marka/model/yıl/motor/donanım/vites/ekspertiz) ve isteğe bağlı fiyat modeli (km-fiyat bantları) vardır. Kurala uyan ilan, referans fiyattan en az `market_min_profit_tl` (varsayılan 100.000 TL) ucuzsa **ve** emsallerin en ucuz çeyreğindeyse galericinin sohbetine gider. Başlığında "acil/kelepir" geçen kurala uyan ilan fiyat şartsız gider. Mesajlar 08–23 arası. |

Her mesajın altında 👍/👎 düğmesi vardır; oylar `feedback` tablosuna yazılır, günlük yönetici özetinde sayılır.
Her galericiye 22:00'de kısa özet gider ("bugün size N ilan gönderdim, K tanesi güçlü fırsat").

## Galerici ekleme
Bkz. [GALERICI_EKLEME.md](GALERICI_EKLEME.md): galerici bota `/start` yazar (bot ona numarasını söyler, sana da bildirir),
Türkçe form doldurulur, `galerici_ekle.py` profili üretir. Profil `DEALERS_JSON` (Railway) ya da `dealers.json` (yerel) içindedir.

## Ayarlar
Varsayılanlar CANLI değerlerdir (`src/config.py`, her satırda açıklama var). Railway'de yalnız şunlar tanımlı olmalı:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PROXY_URL`, `DEALERS_JSON`, `TARGET_URL`, `DB_PATH=/data/arbitrage_db.sqlite`,
`HEADLESS=false`, `BROWSER_CHANNEL=chrome`, `PERPLEXITY_API_KEY`. Başka bir değişken kodu ezer; değiştirmek için koddaki varsayılanı değiştirin.

## İnternet tahmini yedeği (Perplexity)
Yalnız arabam'da **10'dan az aynı donanım emsali** varsa (ve site engelli değilse) çalışır; galerici fiyat modeli, hedef fiyat ya da acil başlık
zaten karar veriyorsa sorulmaz. Ilan tahminden **%25–%40** ucuzsa (galericide ayrıca TL tabanı) mesaj gider; mesajda 🔮 "tahmin, kesin değil" etiketi
olur, genel modda adayın puanı %25 düşük sayılır. Cevaplar 7 gün (bilinmeyen 1 gün) önbellekte tutulur, turda en çok 15 sorgu.
Anahtar boşsa kapalıdır. Ölçüm (25.10.2026, 13 araç): cevap verdiğinde arabam ortancasına yakın (tipik hata %5–8), ama soruların ~%25–55'ine cevap verir
ve ilan satırı vermez; nadir araçta yuvarlak sayılar çıkar. 👍/👎 sayıları günlük özette "tahminli mesajlar" olarak ayrı görünür.

## Sağlık ve bakım
- Yönetici sohbetine: açılış mesajı, günlük özet, engel/captcha uyarısı (+ düzelince ✅), zaman aşımı, sayfa yapısı değişti uyarısı
  (ekspertiz okunamıyorsa), Telegram'a gidemeyen galerici mesajı (galerici botu başlatmamış), yeni `/start`.
- Engel sürerse bot 15/30/60/120 dk aralıkla tek sayfayla yoklar; her yeniden başlatma bu sayacı sıfırlar.
- Veritabanı günde bir kez temizlenir (emsal önbelleği 2 gün, ilanlar 90 gün, mesajlar 180 gün).
- Hata görüntüleri (`logs/`) en çok 20 dosya tutulur.

## Test
```bash
python -m unittest discover -s tests -t .
```
Testler canlı site/Telegram'a bağlanmaz.

## Yerelde çalıştırma
```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt && playwright install chromium
set HEADLESS=true
python main.py
```
Aynı bot token'ı hem Railway'de hem yerelde çalışırsa Telegram 409 verir; Railway'i durdurun.
