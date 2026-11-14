# AGENTS.md — Mars Transit Crew Monitor (NASA Space Apps 2026)

Bu dosyayı Codex doğrudan okur. Claude Code `CLAUDE.md`, Gemini de `GEMINI.md` üzerinden bu dosyaya yönlenir. Buradaki kurallar CI tarafından denetlenir; uymayan PR birleşmez.

## 1. Oturum başı
1. Kullanıcı rolünü söylemediyse sor. Rolü tahmin etme.
2. `git fetch origin && git switch develop && git pull --ff-only`
3. Görevi tek alana ve tek modüle indir. Kod yazmadan önce kısa planı göster.

## 2. Roller ve alanlar
| Kişi | Rol | Alan(lar) | Dosyalar |
|---|---|---|---|
| Barış | Backend ve mantık | `backend` | `backend/` (models.py hariç), `tests/` (test_models.py hariç), `requirements.txt` |
| Efe | Arayüz, entegrasyon, birleştirme | `frontend`, `infra` | `frontend/` · `.github/`, `tools/`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `pyproject.toml`, sahipsiz kök dosyalar |
| Enis | Veri doğrulama ve teslim | `data`, `delivery` | `data/` · `docs/` (contract.json hariç), `README.md`, `reports/`, `research_notes/` |
| Barış + Efe birlikte | API sözleşmesi | `contract` | `backend/models.py`, `docs/contract.json`, `tests/test_models.py` |

Kendi alanın dışındaki dosyayı değiştirme. Gerekiyorsa değiştirmeden sahibine ne istediğini yazan bir GitHub issue aç.

## 3. Branch modeli
- `main`: demo/teslim sürümü. Yalnızca `develop` → `main` release PR'ı ile güncellenir (merge commit). Doğrudan push yok.
- `develop`: varsayılan ve entegrasyon branch'i. Yalnızca PR ile, **squash** birleştirilir. Doğrudan push yok.
- İş branch'i: `develop`'tan açılır, işi bitince silinir. Biçim:

```
<tür>/<alan>/<kısa-açıklama>
tür:   feat | fix | refactor | test | docs | chore
alan:  backend | frontend | data | delivery | contract | infra
kısa-açıklama: küçük harf, rakam ve tire; en fazla 40 karakter
```

Örnekler: `feat/backend/spo2-consecutive-rule`, `fix/frontend/alert-list-empty-state`, `docs/delivery/data-sources`, `feat/contract/add-eva-mode-field`, `chore/infra/ci-cache`.

## 4. Commit ve PR
- Commit mesajı: Conventional Commits, `tür(alan): kısa özet` (emir kipi, en fazla 72 karakter). Örnek: `feat(backend): SpO2 için art arda ölçüm sayacı ekle`
- PR başlığı aynı biçimde olur ve branch'in tür/alanıyla **aynı** olmalı. Squash sonrası `develop` geçmişine bu başlık yazılır.
- PR hedefi her zaman `develop`. Bir PR = tek alan, tek konu, mümkünse 300 satırın altında.
- Push etmeden önce yerelde çalıştır:

```bash
ruff format . && ruff check .
python -m pytest -q
python tools/check_paths.py "$(git branch --show-current)" --title "<PR başlığı>"
```

- CI'deki zorunlu kontroller: `scope` (branch adı, başlık, alan), `lint` (ruff), `tests` (pytest). Üçü yeşil olmadan birleşmez. PR'ı Efe birleştirir; `contract` PR'ı için diğer sözleşme sahibinin onayı gerekir.

## 5. Kod kalitesi
**Python**
- Python 3.11+, tip ipuçları zorunlu, `ruff` temiz olmalı (satır sınırı 110).
- Küçük ve tek işlevli fonksiyonlar. Sihirli sayı yok: eşikler isimli sabit veya `ThresholdRule` olur.
- Tüm dosya işlemlerinde `encoding="utf-8"`.
- `print` ile hata ayıklama, yorum satırına alınmış kod ve kullanılmayan import bırakma.
- Yeni davranış = yeni veya güncellenmiş test.

**Backend**
- Sunucu `127.0.0.1:8000`. SSE döngüsünde `time.sleep` değil `asyncio.sleep` kullanılır.
- `StaticFiles` mount'u `main.py`'nin en altında olur.
- Eksik veri asla "normal" sayılmaz: status `unknown`.
- Uyarılar art arda ölçümle tetiklenir ve en az bir maddelik `why` kanıt listesi taşır.

**Frontend**
- CDN yok, bundler yok. Chart.js yerel dosyadan yüklenir: `frontend/vendor/`.
- Global değişken yok, `const`/`let` kullan. Alan adları `docs/contract.json` ile birebir aynı olur.
- Simüle veri ekranda "SİMÜLE" etiketiyle gösterilir.

**Genel**
- API anahtarı repoya ve tarayıcı koduna konmaz.
- Eşik değerleri kaynaksız kesinmiş gibi yazılmaz. Kaynak yoksa `source="HİPOTEZ"`.

## 6. Sözleşme
- Tek doğruluk kaynağı `backend/models.py` (Pydantic v2).
- Değişince `python -m backend.models` çalıştır. `docs/contract.json` aynı commit'te güncellenir. `test_models.py` eskimiş sözleşmeyi yakalar.
- Sözleşme değişikliği yalnızca `feat|fix/contract/...` branch'inde yapılır.

## 7. Release takvimi (HİPOTEZ, ekip değiştirebilir)
`develop` → `main` release PR'ı şu kontrol noktalarında açılır: Cmt 20:30, Paz 00:00, Paz 09:30, Paz 13:00.
Paz 13:00'te özellik kilidi başlar: `FEATURE_FREEZE=true` repo değişkeniyle CI `feat/` ve `refactor/` PR'larını reddeder. Son release en geç Paz 18:00'de yapılır.

## 8. Görev sonu
- Tek bir doğrulama çalıştır (pytest veya curl) ve sonucunu raporla.
- Yatmadan önce 3 satır not bırak: ne bitti, ne yarım kaldı, sıradaki ne.

## 9. Çalıştırma
```bash
pip install -r requirements.txt ruff==0.8.6
python -m pytest -q
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
