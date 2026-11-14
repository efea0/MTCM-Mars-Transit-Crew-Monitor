# Mars Transit Crew Health & Cabin Air Monitor

NASA Space Apps 2026. Mars transit aracında 4 astronotun sağlığını ve kabin havasını izleyen sistem. Tüm veriler **simüledir**.

```bash
pip install -r requirements.txt
python -m pytest -q
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Ekip çalışma kuralları ve branch yapısı: [AGENTS.md](AGENTS.md). (Bu dosyanın sahibi: Enis)
