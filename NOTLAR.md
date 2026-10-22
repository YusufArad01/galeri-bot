# Notlar ve plan

## Proxy rotasyonu (hazır, Railway'e GÖNDERİLMEDİ)
Ayrı dalda: `git log proxy-rotasyonu` (komut: `git checkout proxy-rotasyonu`).
- Her gün listedeki sıradaki proxy kullanılır (Türkiye saatiyle gün), gün boyu aynı adres.
- Proxy üst üste hata verirse (5+) aynı gün sıradakine geçilir.
- Ay başında yeni liste verilince eskiler rotasyondan çıkar.
- Liste biçimleri: `adres:port:kullanici:sifre` veya `http://kullanici:sifre@adres:port`; ayraç virgül/boşluk/satır sonu.
- Devreye almak için: dalı ana dala al, Railway'de `PROXY_URL` değişkenine listeyi yaz.

Plan: önce tek proxy ile Cloudflare'in geçilip geçilmediği denenecek; geçerse 50'lik paket.

## Proxy otomatik alımı (fikir, YAZILMADI)
ProxyTurk panelindeki API anahtarıyla her ay bakiyeden proxy alma.
Yazmadan önce: API'nin proxy siparişini/listesini destekleyip desteklemediği dökümantasyondan doğrulanmalı.
Koruma şartları: ayda en fazla 1 sipariş, tek sipariş fiyat üst sınırı, öncesinde/sonrasında Telegram mesajı, hatada tekrar deneme yok.

## Yapılacaklar (sırayla)
1. Kilometre düzeltmesi (çok km yapmış araçlar "ucuz" görünüyor) — dal: `gelistirme`
2. Galerici modunu arabam benzer ilan karşılaştırmasına bağlamak (şu an eski Perplexity)
3. Müşterinin kuralı Telegram'dan ekleyip değiştirebilmesi
4. Arabam resmî veri / iş ortağı yolu (EİDS entegrasyonu olan firma) araştırması
5. Gerçek galericiyle pilot, ilk 2 hafta mesajları "iyi/boş" diye işaretleme

## Kalite ölçümü (22-23.10.2026, 59 ilan)
Karşılaştırılabilen 21 ilandan %10+ ucuz 4, %15+ ucuz 1. Üst sıradaki ilanların çoğunda km yüksek;
km toleransı ±%40 olduğundan "sadece çok yürüdüğü için ucuz" görünenler var.
