# Notlar

## Proxy rotasyonu (hazır, Railway'e GÖNDERİLMEDİ)
Ayrı dalda: `git checkout proxy-rotasyonu`.
- Her gün listedeki sıradaki proxy kullanılır (Türkiye saatiyle gün); proxy üst üste 5+ hata verirse aynı gün sıradakine geçilir.
- Liste biçimleri: `adres:port:kullanici:sifre` veya `http://kullanici:sifre@adres:port`; ayraç virgül/boşluk/satır sonu.
- Devreye almak: dalı ana dala al (güncel koda uyarlanmalı), Railway'de `PROXY_URL`'ye listeyi yaz.
- Karar (kullanıcı): gerçek müşteri ve sermaye gelince; şimdilik tek proxy.

## Ertelenenler
- Fiyat düşüşü takibi, "ilan hâlâ satışta mı" kontrolü: gerek yok denildi.
- ProxyTurk API ile otomatik aylık proxy alımı: yazılmadı (API'nin sipariş desteği doğrulanmadı).
- Arabam resmî veri / iş ortağı yolu: kullanıcı kararı gereği şimdilik yok.

## Dersler
- Railway değişkenleri koddaki varsayılanı ezer; ayar değiştirince bunu unutma.
- Tek proxy saatte ~120 istekten fazlasını kaldırmıyor; 14:46'da (24.10) ~250 istek/saat ve sık yeniden başlatma Cloudflare engeli getirdi.
- `RESCAN_LISTINGS` değeri değişince bilinen ilanlar yeniden taranır (istek yükü); gereksiz değiştirme.
- Her deploy bekleme sayacını sıfırlar; engel sürerken gereksiz push yapma.
