# Yeni galerici ekleme

Her galerici için 4 adım, yaklaşık 15 dakika.

1. **Galerici bota yazar.** Telegram'da botu açıp "merhaba" yazar.
2. **Telegram numarasını bul.**
   `TELEGRAM_BOT_TOKEN=... python chat_id_bul.py` → galericinin numarası ve adı listelenir.
3. **Formu doldur.** `galerici_formu_ornek.json` dosyasını kopyala, galericinin söylediklerini yaz.
4. **Profile çevir ve yükle.**
   ```bash
   python galerici_ekle.py galerici_form.json            # dealers.json'a ekler, özet yazar
   python galerici_ekle.py galerici_form.json --railway  # canlıya yükler (bot yeniden başlar)
   ```
   Yüklemeden önce özeti kontrol et. Canlıya yüklemek mesajları anında değiştirir.

## Form alanları (her kural için)

| Alan | Ne demek | Örnek |
|---|---|---|
| `marka`, `model` | Marka ve model (zorunlu: marka) | Volkswagen, Passat |
| `yil` | Model yılı aralığı | [2015, 2017] |
| `motor`, `donanim` | Seçenekler `\|` ile ayrılır | "1.6 TDI\|1.4 TSI", "Comfortline\|Highline" |
| `vites` | DSG yazılırsa "yarı otomatik" de kabul edilir | "DSG" |
| `km_araligi` | İsteğe bağlı km sınırı | [100000, 180000] |
| `degisen_en_fazla`, `boya_en_fazla` | İlan sayfasındaki parça listesine göre | 0, 2 |
| `boya_yerleri` | Boya yalnız bu sözcükleri içeren parçalarda olabilir | ["çamurluk", "kapı"] |
| `yasak_parcalar` | Boyalı/değişen parça adında geçerse elenir | ["tavan", "kaput"] |
| `tramer_en_fazla` | Tutar yazılıysa sınırı aşan elenir; yazılmamışsa uyarıyla gider | 10000 |
| `haric_kelimeler` | Başlıkta geçerse kural uymaz | ["variant"] |
| `min_kar_tl` | Referans fiyattan en az bu kadar ucuz olmalı (varsayılan 100.000) | 100000 |
| `fiyat_modeli` | Galericinin km-fiyat bantları (bin km, bin TL). Yoksa canlı emsal kıyası kullanılır | örnek dosyada |

`fiyat_modeli` ayrıntısı: `ref_yil` bantların geçerli olduğu model yılı, `yil_basina_yuzde` yıl farkı etkisi,
`boyali_parca_tl` boyalı parça başı düşüş, `dogu_dusus_tl` Doğu/Güneydoğu illerinde indirim,
`buyuksehir_disi_dusus_tl` büyükşehir dışı indirim, `primler` başlıkta geçen sözcüğe göre eklenen TL.

## Her galericide otomatik olanlar
- Mesajlar yalnız 08:00–23:00 arasında gider.
- Başlığında "acil", "kelepir" geçen ve kurallara uyan ilan fiyat şartı aranmadan gider.
- Kara liste kelimeleri (pert kaydı, çekme belgeli...) ilanı eler.
- İlan emsallerin en ucuz çeyreğinde değilse fırsat sayılmaz.

## Kapasite
Her farklı arama sayfası tur başına yaklaşık 2 liste isteği demektir. Tek proxy ile **en çok 10 farklı arama sayfası**
(yaklaşık 5 galerici) öneriyorum; fazlası arabam'ın engelini tetikler. Betik özetinde sayı yazılıdır.
