# Aydın'a not: data/reference/messages.json (14 Kasım)

`data/reference/messages.json` hazır (branch `docs/reference/alert-message-texts`).
Ekranda görünen tüm metinler (uyarı mesajı, "Neden?" notu, öneri, EVA, Dünya paneli) burada.
Kodda uyarı metni sabit yazılmasın, buradan okunsun. **Sözleşmeye dokunulmadı.**

> **PR #29'u "Squash and merge" ile birleştir.** Branch geçmişinde test kalıntısı bir `base` commit'i var
> (yazar `t@example.com`, 42 dosyayı siliyor, sonraki commit geri alıyor). Toplam fark temiz, ama merge commit ile
> birleşirse bu commit develop geçmişine girer.

## Sözleşme ile ilgili noktalar (karar senin, `contract` alanı)

1. **Öneri alanı yok (açık).** `Alert` modelinde `recommendation` yok. Şimdilik öneri,
   `global.alert_message_format` (`"{message}. {recommendation}"`) ile `Alert.message`'ın sonuna ekleniyor.
   Ayrı alan istersen contract PR'ı gerekir. PR #26 bu alanı eklemiyor.
2. **O₂ birimi ve `pio2` metriği (PR #26 ile kapanıyor).** PR #26 `pp_o2` ve `cabin_pressure` birimini mmHg yapıyor
   ve `Metric` listesine `pio2` ekliyor. #26 birleşince bu nokta kapanır, senden iş beklemez.
3. **warning seviyesi (açık).** Metinler yalnızca `caution` (İzle) ve `critical` (Eylem) için yazıldı.
   rules.py `warning` üretirse `critical` metni kullanılsın ya da haber ver, ayrı metin yazarım.
4. **Etkinlik `unknown` iken hipoksi notu (açık, metin bende).** Sözleşmeye göre etkinlik bilinmiyorsa
   "dinleniyor" varsayılmaz. Bu durum için ayrı bir "Neden?" metni yazacağım; gelene kadar nabız ve solunumu
   kanıt olarak yazma.

## Oturumuna yapıştırabileceğin prompt

```
data/reference/messages.json eklendi (reference alanı). Kurallar:
1. rules.py, ranges.json gibi messages.json'u da açılışta bir kez yükler (encoding="utf-8").
   Uyarı metni koda sabit yazılmaz; str.format ile doldurulur. Yer tutucuların hepsi
   dosyadaki "placeholders" bölümünde; eşik sayıları ranges.json'dan doldurulur.
2. Eşleme:
   - Alert.message = global.alert_message_format; message ve recommendation, Alert.severity
     anahtarıyla seçilir (caution | critical; warning gelirse critical).
   - Alert.why[].note = ilgili why_note. olasi_hipoksi'de CrewMember.activity (sözleşme 0.2.0, PR #26)
     "resting" veya "sleep" ise why_note_rest, "exercise" ise why_note_exercise
     (#21: egzersizde nabız/solunum kanıt olarak yazılmaz). "unknown" ise nabız/solunum kanıt
     yazılmaz; metni Enis ekleyecek (açık nokta 4).
     radyasyon_etkisi: olay kaynaklıysa why_note_event, kariyer kaynaklıysa why_note_career.
   - Kabin uyarıları: cabin_alerts anahtarları ranges.json "kurallar" rule id'leriyle aynı
     (ppco2-caution, ppco2-limit, pio2-normoxia-low, pio2-hypoxia-low, pio2-normoxia-high).
   - Kişi kararları: health_decisions anahtarları (olasi_hipoksi, olasi_co2_etkisi, isi_sapmasi,
     kalp_yuku, radyasyon_etkisi); her birinin "metric" alanı Alert.metric için.
   - Eksik veri: global.no_data_message / no_data_note; Status.unknown ekranda global.status_names.unknown.
   - global.banner_simulated, banner_disclaimer, clock_label arayüzde her zaman görünür.
   - comms.* Dünya paneli (P3); sender adları comms.sender_names.
3. Bir yer tutucu eksik kalırsa (KeyError) uyarı düşmesin: şablonu ham hâliyle göster ve logla.
   Her şablonun örnek değerlerle hatasız doldurulabildiğini gösteren bir pytest yaz.
```

## Barış için

- Senaryo dosyalarındaki beklenen karar tablosu `health_decisions` ve `cabin_alerts` anahtarlarını kullanabilir.
- `comms.statuses` (queued / in_transit / delivered / replied) yalnızca öneri.
  Senin comms.py şartnamendeki adlar farklıysa söyle, metni değiştiririm.
