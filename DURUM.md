# DURUM — arbitrage_engine

Son güncelleme: 2026-10-24 21:00 (TR)

## Canlıdaki durum
- Railway (`truthful-trust` / `galeri-bot`): son deploy SUCCESS = `main` HEAD `7c2f278`. Loglar `railway_auth.get_token()` + GraphQL `deploymentLogs` ile okunur (çalışıyor; `limit` en çok 5000).
- `BOT_MODE=compare,dealer`. Tek ProxyTurk proxy (IP 153.56.219.29, canlı).
- **ENGEL:** 24.10 14:46'dan beri Railway→arabam Cloudflare 403. Sebep: benim artırdığım istek yükü (~250/saat) ve sık yeniden başlatma. Önlemler canlıda: tur başı 20 ilan, `DEALER_PAGES=2`, tur arası 20 dk, engelde kalan sayfalar istenmez, engel sürerse 15/30/60/120 dk geri çekilme. **Yeniden başlatma bekleme sayacını sıfırlar: gereksiz deploy yapma.**
- **Karar (kullanıcı): 25.10 öğlen 12:00'ye kadar bekle.** Kalkmadıysa ProxyTurk'ten yeni IP; yedek proxy kodu `proxy-rotasyonu` dalında (birleşmemiş).
- Compare: aynı donanım emsali ≥10 ilan, ortancadan ≥%15 ya da (≥150 bin TL ve ≥%8), ≤%30, ilan emsal alt çeyreğinde, ≤12 yaş/≤200k km, günde ≤10, 90 dk'da 1, 08–23 TR.
- Dealer: demo galerici `vw-uzmani` (9 kural; Passat sedan/Variant/Golf/Polo/Tiguan fiyat modelli + eski emsal kuralları). Karar: referanstan ≥100.000 TL ucuz + emsal alt çeyreğinde + %3 alt sınır; başlığında acil/kelepir geçen kurala uyan ilan fiyatsız gider; 08–23 TR; chat_id boş → mesaj yöneticiye. Mesaj metni sadeleştirildi.
- Tuzaklar: Railway değişkenleri koddaki varsayılanı ezer; `RESCAN_LISTINGS` değeri değişince bilinen VW ilanları yeniden taranır (istek yükü); `skipDeploys:true` ile değişken yazılınca değer sonraki deploy'da devreye girer.

## Sıradaki işler
- **B1** 25.10 12:00: engel kalktı mı bak (Railway log). Kalkmadıysa kullanıcıya yeni IP/yedek proxy öner. Bekleyen: ben.
- **B2** Pazartesi/salı Adana Oto Galericiler Odası Başkanı sunumu: tek dosyalık koyu temalı HTML (hafızadaki sunum stili + `impeccable` skill), gerçek mesaj örnekleri (Passat 1.325.000/1.360.000 TL, Megane), fiyat önerisi, 2 hafta ücretsiz pilot. Bekleyen: ben (kullanıcı onayladı: önce mesaj sadeleştirme + galerici ekleme düzeni, sonra sunum).
- **B3** Hazırlık ~%50 (demo ~%65). Eksikler: erişim güvenilirliği (tek proxy), galerici ekleme düzeni ✔ (hazır), fiyat/pilot, arabam kullanım şartları riski (kullanıcıya söylendi).
- **B4** Yedek proxy (`proxy-rotasyonu`) güncel sürüme uyarla + test. Bekleyen: kullanıcı kararı (engel kalkmazsa).
- **A1** Gerçek galerici profili + `chat_id`: `GALERICI_EKLEME.md` akışı (`chat_id_bul.py` → form → `galerici_ekle.py --railway`). Tek proxy ile en çok ~10 farklı arama sayfası.
- **A2** Gelen mesajların kalitesini tek tek incele (kullanıcı değerlendirecek).
- **A3** Fiyat düşüşü takibi (liste sayfasındaki fiyat ↔ kayıtlı fiyat). Ertelendi.
- **A4** Arabam resmî veri / iş ortağı yolu. Ertelendi.
- **A5** Kökteki izlenmeyen dosyalar (`dump_vavacars*.py`, `test_parser.py`): sil/commit kararı.

## Son yapılan işler
- 24.10 20:33 dealer mesajı sadeleştirildi; `galerici_ekle.py`, `chat_id_bul.py`, `galerici_formu_ornek.json`, `GALERICI_EKLEME.md` `7c2f278`.
- 24.10 17:21 engelde sayfa isteme + geri çekilme `0d2c166`.
- 24.10 14:35 dealer fiyat modeli (km-fiyat bantları; yıl, boya, bölge, premium) `01d4238`.
- 24.10 14:22 emsal alt çeyreği şartı `36a1398`; 13:52 acil kelimeli başlık `025e4b8`.
- 24.10 11:59 canlı emsal kontrolü + TL taban; 09:38 emsaller aynı donanım adı; 05:02 dealer mesajı 08–23.
- 23.10 gece ProxyTurk tek proxy bağlandı `ec01dbf`.
