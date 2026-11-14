# Senaryo dosyaları

Tüm değerler simüle. Eşikler `data/reference/ranges.json`, kabin dengesi `backend/atmosphere.py` (#14) ile hizalı.

| Senaryo | Durum | Süre (görev dk) |
|---|---|---|
| `nominal` | Hazır | 180 |
| `hypoxia` | Hazır | 1800 |
| `hypercapnia` | Hazır | 670 |
| `solar_storm` | Hazır | 780 |
| `sleep_deprivation` | Hazır (eşikler HİPOTEZ) | 1800 |
| `sensor_dropout` | **Yok**, bu PR'ın kapsamı dışında | - |

Ortak kurallar: her senaryo en fazla 1800 görev dakikasına sığar (testle denetlenir), hacim 75 m³,
ölçüm aralığı 5 görev dakikası, motorun limit aşımı kademesi `critical`.

## sleep_deprivation

Kişi bazlı uyku süresi: her uyku dönemi sonunda ölçülür (`activity = sleep` süreleri toplamı), art arda 2 dönem
< 6 saat İzle (`caution`), < 5 saat Eylem (`critical`). Eşikler ranges.json `sleep-short-*` kurallarından
(HİPOTEZ, ekip kararı, doğrulanmadı). Dört kişi dört durum: Can Eylem, Ece İzle, Ayşe sağlıklı, Mert'in tek kısa
uykusu uyarı çıkarmaz (art arda şartı). Kural ranges.json'da `warning` adıyla duruyor; senaryoda Eylem `critical`
yazılır (#14 motoru ve kural motoru `warning` üretmez). `sleep_hours` henüz sözleşmede `Metric` değil
(testlerde xfail, Aydın).

## Yazılmayan senaryo

`models.Scenario` `sensor_dropout` adını tanımlıyor; dosyası bu PR'da yok. Eksikliği gizlemek yerine burada yazıyoruz.

- **`sensor_dropout`:** Ölçüm aralığı (5 görev dakikası) kararlaştığı için yazılabilir; ayrı bir PR'da gelecek.
  Beklenen davranış: eksik veri `unknown` olur, asla `nominal` sayılmaz (`Reading` doğrulayıcısı).

Bir senaryo eklenirken `tests/test_scenarios.py` içindeki `REQUIRED_SCENARIOS` kümesine de eklenmelidir.
