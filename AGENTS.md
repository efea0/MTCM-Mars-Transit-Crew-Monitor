# AGENTS.md — NASA Space Apps 2026 (Mars transit, ~30 saatlik sprint)

Bu dosyayı Claude Code (`CLAUDE.md` üzerinden), Codex ve Gemini (`GEMINI.md` üzerinden) otomatik okur.

## 1. Önce kim olduğunu sor
Kullanıcı oturum başında rolünü söylemediyse sor. Rolü tahmin etme.

| Kişi / iş | Sorumluluk | Branch öneki | Dokunabileceği dosyalar |
|---|---|---|---|
| Barış | Backend ve mantık | `baris/` | `backend/atmosphere.py`, `rules.py`, `simulator.py`, `comms.py`, `main.py`, `tests/` (`tests/test_models.py` hariç) |
| Efe | Arayüz, entegrasyon, main'e birleştirme | `efe/` | `frontend/` |
| Enis | Veri doğrulama ve teslim | `enis/` | `data/`, `docs/` (`docs/contract.json` hariç), `README.md`, `reports/`, `research_notes/` |
| Ortak sözleşme (yalnızca Barış + Efe birlikte) | API sözleşmesi | `contract/` | `backend/models.py`, `docs/contract.json`, `requirements.txt`, `tests/test_models.py` |
| Altyapı (Efe) | Repo kuralları, CI | `infra/` | `.github/`, `tools/`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.gitignore`, `.gitattributes` |

Başka rolün dosyasını değiştirme. Gerekiyorsa değiştirmeden sahibine ne istediğini yazan bir not veya GitHub issue bırak.

## 2. Git akışı
- `main`'e doğrudan push yok. `main` her zaman çalışır durumda kalır.
- İşe başlamadan: `git checkout main && git pull origin main`.
- Kendi önekinle kısa ömürlü branch aç: `git checkout -b baris/rules-spo2`.
- Küçük ve sık commit at, push et, `main`'e PR aç.
- Commit öncesi çalıştır: `python tools/check_paths.py <branch-adı>`. `KAPSAM DIŞI` çıkarsa o dosyaları geri al.
- PR'ı Efe birleştirir. `path-guard / scope` ve `path-guard / tests` kontrolleri yeşil olmadan birleştirme yok.

## 3. Sözleşme kuralları
- Tek doğruluk kaynağı: `backend/models.py` (Pydantic v2).
- Değişince `python -m backend.models` ile `docs/contract.json` yeniden üretilir; ikisi aynı commit'te olur.
- Frontend alan adlarını `docs/contract.json`'dan okur, uydurmaz.
- Sözleşmeye bir alan lazımsa kendi branch'inde ekleme; Barış ve Efe'ye `contract/` PR'ı öner.

## 4. Kod kuralları
- CDN yok, bundler yok. Chart.js yerel dosyadan: `frontend/vendor/`.
- Tüm dosya işlemlerinde `encoding="utf-8"`.
- Sunucu `127.0.0.1:8000`.
- SSE döngüsünde `time.sleep` değil `asyncio.sleep`.
- `StaticFiles` mount'u `main.py`'nin en altında olur.
- Eksik veri asla "normal" sayılmaz: status `unknown`.
- Uyarılar art arda ölçümle tetiklenir ve `why` kanıt listesi taşır (en az 1 madde).
- Simüle veri ekranda "SİMÜLE" etiketiyle gösterilir.
- API anahtarı repoya ve tarayıcı koduna konmaz.
- Eşik değerleri kaynaksız kesinmiş gibi yazılmaz. Kaynak yoksa `source="HİPOTEZ"`.

## 5. Görev biçimi
- Her görevde tek modüle odaklan.
- Kod yazmadan önce kısa planı göster.
- Bitince pytest veya curl ile tek bir doğrulama çalıştır ve sonucu raporla.
- Yatmadan önce 3 satırlık not bırak: ne bitti, ne yarım kaldı, sıradaki ne.

## 6. Çalıştırma
```bash
pip install -r requirements.txt
python -m pytest -q
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
