# Senaryo dosyaları

Tüm değerler simüle. Eşikler `data/reference/ranges.json`, kabin dengesi `backend/atmosphere.py` (#14) ile hizalı.

| Senaryo | Durum | Süre (görev dk) |
|---|---|---|
| `nominal` | Hazır | 180 |
| `hypoxia` | Hazır | 1800 |
| `hypercapnia` | Hazır | 670 |
| `solar_storm` | Hazır | 780 |
| `sleep_deprivation` | **Yok**, bu PR'ın kapsamı dışında | - |
| `sensor_dropout` | **Yok**, bu PR'ın kapsamı dışında | - |

Ortak kurallar: her senaryo en fazla 1800 görev dakikasına sığar (testle denetlenir), hacim 75 m³,
ölçüm aralığı 5 görev dakikası, motorun limit aşımı kademesi `critical`.

## Yazılmayan iki senaryo

`models.Scenario` bu iki adı tanımlıyor; dosyaları henüz yok. Eksikliği gizlemek yerine burada açık yazıyoruz.

- **`sleep_deprivation`:** Ranges.json'da uykuya özel bir eşik yok (yalnızca CO₂ etkisi notunda "uyku azalması"
  geçiyor). Eşikler kararlaştırılmadan beklenen karar tablosu yazılamaz. Sözleşmede `Activity.sleep` var (#26).
- **`sensor_dropout`:** Ölçüm aralığı (5 görev dakikası) kararlaştığı için yazılabilir; ayrı bir PR'da gelecek.
  Beklenen davranış: eksik veri `unknown` olur, asla `nominal` sayılmaz (`Reading` doğrulayıcısı).

Bir senaryo eklenirken `tests/test_scenarios.py` içindeki `REQUIRED_SCENARIOS` kümesine de eklenmelidir.
