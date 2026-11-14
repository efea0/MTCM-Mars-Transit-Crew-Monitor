# Senaryo dosyaları

Tüm değerler simüle. Eşikler `data/reference/ranges.json`, kabin dengesi `backend/atmosphere.py` (#14) ile hizalı.

| Senaryo | Durum | Süre (görev dk) |
|---|---|---|
| `nominal` | Hazır | 180 |
| `hypoxia` | Hazır | 1800 |
| `hypercapnia` | Hazır | 670 |
| `solar_storm` | Hazır | 780 |
| `sensor_dropout` | Hazır | 120 |
| `sleep_deprivation` | **Yok**, kapsam dışı | - |

Ortak kurallar: her senaryo en fazla 1800 görev dakikasına sığar (testle denetlenir), hacim 75 m³,
ölçüm aralığı 5 görev dakikası, motorun limit aşımı kademesi `critical`.

## Yazılmayan senaryo

`models.Scenario` `sleep_deprivation` adını tanımlıyor; dosyası henüz yok. Eksikliği gizlemek yerine burada açık yazıyoruz.

- **`sleep_deprivation`:** Ranges.json'da uykuya özel bir eşik yok (yalnızca CO₂ etkisi notunda "uyku azalması"
  geçiyor). Eşikler kararlaştırılmadan beklenen karar tablosu yazılamaz. Sözleşmede `Activity.sleep` var (#26).

## sensor_dropout

Üç sensör durumu (`missing`, `stale`, `sensor_fault`) ve veri geri gelişi. Beklenen davranış `expected_data_status`
bölümünde: kalitesi `ok` olmayan okuma ilk ölçümden itibaren `unknown` olur, asla `nominal` sayılmaz (`Reading`
doğrulayıcısı), art arda 3 geçersiz okumada `caution` seviyesinde "veri yok" uyarısı (`missing-<metric>`) çıkar ve `sensor_fault` değeri kural motoruna girmez.
Davranış kural motoruyla (#43) ve sözleşmeyle (`Comparator.missing`) uyumlu; kurallar `data_quality_rules` bölümünde.

Bir senaryo eklenirken `tests/test_scenarios.py` içindeki `REQUIRED_SCENARIOS` kümesine de eklenmelidir.
