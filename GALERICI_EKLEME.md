# Yeni galerici ekleme

Her galerici için 4 adım, yaklaşık 15 dakika.

1. **Galerici bota yazar.** Telegram'da botu açıp `/start` yazar. Bot ona sohbet numarasını söyler, **sana da**
   "👤 Yeni /start: ad (@kullanıcı) chat_id=123456" diye bildirir. Numarayı oradan al.
2. **Formu doldur.** `galerici_formu_ornek.json` dosyasını kopyala, galericinin söylediklerini yaz
   (`telegram` alanına numara).
3. **Profile çevir.**
   ```bash
   python galerici_ekle.py galerici_form.json            # dealers.json'a ekler/günceller, özet yazar
   ```
   Özet kaç kural ve kaç farklı arama sayfası olduğunu söyler. Hatalı form burada yakalanır.
4. **Canlıya yükle** (bot yeniden başlar, bekleme sayacı sıfırlanır; engel sürerken yapma):
   ```bash
   python galerici_ekle.py galerici_form.json --railway
   ```
   Ardından eski ilanların yeni kurallarla yeniden değerlendirilmesini istiyorsan Railway'de
   `RESCAN_LISTINGS` değişkenine o markanın adını (ör. `Passat,Golf,v1`) yaz; değeri her değiştirişinde bir kez çalışır.

## Form alanları (her kural için)

| Alan | Ne demek | Örnek |
|---|---|---|
| `marka`, `model` | Marka (zorunlu), model | Volkswagen, Passat |
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
- Mesajlar yalnız 08:00–23:00 arasında gider; her mesajın altında 👍/👎 düğmesi var, 22:00'de kısa günlük özet gelir.
- Başlığında "acil", "kelepir" geçen ve kurallara uyan ilan fiyat şartı aranmadan gider.
- Kara liste kelimeleri (pert kaydı, çekme belgeli...) ilanı eler.
- İlan emsallerin en ucuz çeyreğinde değilse fırsat sayılmaz.
- Mesajda ilan sayfasında telefon varsa 📞 satırı çıkar; yoksa yalnız link gider.
- Galerici botu başlatmamışsa (Telegram reddederse) sana uyarı gelir.

## Kapasite
Her farklı arama sayfası tur başına yaklaşık 2 liste isteği demektir. Tek proxy ile **en çok 10 farklı arama sayfası**
(yaklaşık 5 galerici) öneriyorum; fazlası arabam'ın engelini tetikler. Betik özetinde sayı yazılıdır.
