# AGENTS.md — Mars Transit Crew Monitor (NASA Space Apps 2026)

Bu dosyayı Codex doğrudan okur. Claude Code `CLAUDE.md`, Gemini de `GEMINI.md` üzerinden bu dosyaya yönlenir. Buradaki kurallar CI tarafından denetlenir; uymayan PR birleşmez.

## 1. Oturum başı
1. Kullanıcı rolünü söylemediyse sor. Rolü tahmin etme.
2. `git fetch origin && git switch develop && git pull --ff-only`
3. Görevi tek alana ve tek modüle indir. Kod yazmadan önce kısa planı göster.

## 2. Roller ve alanlar
| Kişi | Rol | Alan | Dosyalar |
|---|---|---|---|
| Aydın | Sürücü: ürün kodunun tek üreticisi, birleştirme | `backend` | `backend/` (models.py ve comms.py hariç), `tests/`, `requirements.txt` |
| | | `frontend` | `frontend/` |
| | | `infra` | `.github/`, `tools/`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `pyproject.toml`, sahipsiz kök dosyalar |
| Barış | Python doğrulayıcı, comms.py | `qa` | `tests/` (test_models.py hariç), `data/scenarios/`, `data/donki/` |
| | | `comms` | `backend/comms.py`, `tests/test_comms.py` |
| Enis | Doğrulama ve teslim | `reference` | `data/reference/` (ranges.json, kaynak/durum alanlarıyla) |
| | | `delivery` | `docs/` (contract.json hariç), `README.md`, `reports/`, `research_notes/` |
| Aydın + bir onaylayıcı | API sözleşmesi | `contract` | `backend/models.py`, `docs/contract.json`, `tests/test_models.py` |

- Ürün kodunu (backend ve frontend) yalnızca Aydın'ın oturumu yazar. Barış'ın ajanı `backend/comms.py` dışında ürün koduna dokunmaz. Hatayı test veya issue olarak bildirir, kodu kendisi düzeltmez.
- Enis'in ajanı kod yazmaz; veri, kaynak ve metin üretir.
- Kendi alanın dışındaki dosyayı değiştirme. Gerekiyorsa sahibine ne istediğini yazan bir GitHub issue aç: başlıkta `[alan]` olsun, gövdede beklenen ve gözlenen davranış yazsın.

## 3. Branch modeli
- `main`: demo/teslim sürümü. Yalnızca `develop` → `main` release PR'ı ile güncellenir (merge commit). Doğrudan push yok.
- `develop`: varsayılan ve entegrasyon branch'i. Yalnızca PR ile, **squash** birleştirilir. Doğrudan push yok.
- İş branch'i: `develop`'tan açılır, işi bitince silinir. Biçim:

```
<tür>/<alan>/<kısa-açıklama>
tür:   feat | fix | refactor | test | docs | chore
alan:  backend | frontend | comms | qa | reference | delivery | contract | infra
kısa-açıklama: küçük harf, rakam ve tire; en fazla 40 karakter
```

Örnekler:
- Aydın: `feat/backend/atmosphere-mass-balance`, `feat/frontend/crew-cards`, `feat/contract/crew-health-decision`
- Barış: `test/qa/consecutive-and-missing-data`, `feat/qa/o2-failure-scenario`, `feat/comms/light-delay-queue`
- Enis: `docs/reference/pio2-ranges`, `docs/delivery/readme-run-steps`

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

- CI'deki zorunlu kontroller: `scope` (branch adı, başlık, alan), `lint` (ruff), `tests` (pytest). Üçü yeşil olmadan birleşmez. PR'ları Aydın birleştirir. `contract` PR'ı Barış veya Enis'ten bir onay almadan birleşmez.

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
- Sözleşme değişikliği yalnızca `feat|fix/contract/...` branch'inde yapılır ve tek başına birleştirilmez.

## 7. Release takvimi (HİPOTEZ, ekip değiştirebilir)
`develop` → `main` release PR'ı şu kontrol noktalarında açılır: Cmt 20:30 (Kontrol 1), Paz 00:00 (Kontrol 2, ilk canlı yayın), Paz 09:30 (Kontrol 3), Paz 13:00 (dondurma), Paz 17:30 (Kontrol 4 sonrası).
Paz 13:00'te Enis özellik dondurmasını ilan eder ve `gh variable set FEATURE_FREEZE --body true` çalıştırılır. Bundan sonra CI `feat/` ve `refactor/` PR'larını reddeder. Son release en geç Paz 19:00'da yapılır.

## 8. Görev sonu
- Tek bir doğrulama çalıştır (pytest veya curl) ve sonucunu raporla.
- Yatmadan önce 3 satır not bırak: ne bitti, ne yarım kaldı, sıradaki ne.

## 9. Çalıştırma
```bash
pip install -r requirements.txt ruff==0.8.6
python -m pytest -q
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
