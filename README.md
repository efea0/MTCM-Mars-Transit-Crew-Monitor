# Mars Transit Crew Health & Cabin Air Monitor

NASA Space Apps 2026. Mars transit aracında 4 astronotun sağlığını ve kabin havasını izleyen sistem. Tüm veriler **simüledir**. Tıbbi tanı değil, karar desteğidir.

## Canlı demo

Barındırma: Render ücretsiz web servisi. Link: **[TODO: ilk yayından sonra eklenecek]**

> **Render'da ilk açılış ~1 dk sürer.** Ücretsiz servis 15 dakika trafik almazsa uyur ve ilk istekte uyanır.
> Panel açıkken her 4 dakikada bir `/health` çağrısı servisi uyanık tutar.

## Yerelde çalıştırma

```bash
pip install -r requirements.txt
python -m pytest -q
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Ekip çalışma kuralları ve branch yapısı: [AGENTS.md](AGENTS.md). (Bu dosyanın sahibi: Enis)
