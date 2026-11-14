# Mars Transit Crew Health & Cabin Air Monitor

**NASA Space Apps Challenge 2026** · Challenge: *Create Health Monitoring Software for Astronauts on Space Missions*

A decision-support dashboard for a 4-person crew on the way to Mars. It reads each astronaut's vital signs
together with the cabin air (O₂, CO₂) and radiation, and tells **which crew member** needs to act, **why**, and **what to do**.
Earth is up to 22 minutes away one way, so the crew cannot wait for an answer.

> **All health and dose data in this app is simulated.** The solar event time and type come from a real NASA DONKI record.
> This is **decision support, not medical diagnosis.** Vital-sign deviation thresholds are demo parameters, not medical thresholds.

## Links

| What | Link |
|---|---|
| Live demo | **[TODO: add after first deploy]** (Render free tier) |
| Project demo (≤ 30 s video or ≤ 7 slides) | **[TODO]** |
| Space Apps project page | **[TODO]** |

> **The live demo takes about 1 minute to open the first time.** The free Render service sleeps after 15 minutes
> without traffic and wakes on the first request. While the dashboard is open, it calls `/health` every 4 minutes
> to keep the service awake.

## What it does

- **Per-person health status.** Heart rate, SpO₂, body temperature and breathing rate for 4 astronauts. Each one is read
  against that person's own baseline: the same heart rate can be normal for one person and a warning for another.
  Status: **Normal / Watch / Action**.
- **Body + cabin in one model.** O₂ use is estimated from each person's heart rate, and that feeds the cabin O₂/CO₂ mass balance.
  Example: *"Ece: stop exercising, her SpO₂ is dropping and cabin PIO₂ is below normoxia."*
- **Two countdowns.** Hours until O₂ reaches its lower limit and until CO₂ reaches its upper limit.
- **"Why?" for every alert.** The evidence (values, baseline, threshold) and the source of the threshold.
- **Exercise does not cause false alarms.** A higher heart rate during exercise is expected and is not flagged as cardiac load.
- **Missing data is never "normal".** A missing sensor shows **NO DATA**.
- **Radiation warning before the dose.** During a solar particle event the system projects the end-of-event dose and
  sends the crew to the storm shelter *before* it is received.
- **Delayed Earth link.** Reports go to Earth through a 3–22 min one-way delay queue (shortened in the demo, and labeled).
- **Other closed environments.** The same O₂/CO₂ engine with a submarine and an underground refuge chamber profile,
  each with its own limits and sources.

## Demo walkthrough

This is the live presentation flow, about 3 minutes. The Space Apps "Project Demo" link is a separate video of 30 seconds or less, or a deck of 7 slides or fewer.

Events are triggered from a hidden demo panel. Randomness uses a fixed seed, and the demo needs neither the internet nor voice. The simulation clock runs at ×600, and the screen says so. A measurement is taken every 5 mission minutes, which is 0.5 s in the demo.

1. **Normal.** The Habitat panel shows four astronauts (Ayşe, Can, Ece, Mert), cabin O₂ and CO₂, and two countdowns.
2. **O₂ generation fails.** Cabin PIO₂ slowly drops and the O₂ countdown starts. CO₂ removal still works, so the CO₂ countdown does not move. Ece is exercising: her heart rate reaches 130 and her breathing 26/min. Neither is counted as an alarm or as hypoxia evidence. When her SpO₂ is 3 points below her own baseline she turns **Watch**, and at 6 points she turns **Action**. The evidence shown is SpO₂ plus cabin PIO₂, and the advice is *"stop exercise, switch to backup O₂"*. This personal alert comes long before the cabin alert turns red.
3. **Same heart rate, different decision.** Ayşe and Mert both have a heart rate of 80. Ayşe is +16 over her baseline and turns **Watch** (cardiac load). Mert is +13 over his and stays **Normal**.
4. **Solar storm.** The real NASA DONKI event (10 May 2024) is replayed. The system projects the end-of-event dose, and when it will pass 200 mSv it says *"go to the storm shelter"* before the dose is received.
5. **EVA view.** The screen shows suit PIO₂ and PICO₂, the remaining supply time and, if needed, *"return to the airlock"*.
6. **Earth link.** A report goes to Earth with a visible one-way delay countdown, and the reply arrives later.
7. **Other environments.** The environment selector switches to a submarine or an underground refuge chamber. The limits and their sources change, while the engine stays the same.

## How it works

```
data/reference/ranges.json ──┐   (limits + sources + verification status)
data/donki/sep_event.json ───┤   (real NASA DONKI solar particle event)
data/scenarios/*.json ───────┤   (demo scenarios, fixed seed)
                             ▼
backend/simulator.py  →  backend/atmosphere.py  →  backend/rules.py  →  backend/main.py  →  frontend/
(vitals, baseline,        (O₂/CO₂ mass balance,     (per-person +         (FastAPI + SSE)     (Habitat panel,
 circadian rhythm,         two countdowns)            cabin decisions,                         EVA view, Earth panel)
 scenario events)                                     3 consecutive readings)
                                                                                 backend/comms.py (delay queue)
```

- **Backend:** Python 3.11+, FastAPI, Server-Sent Events. Data lives in memory and in JSON files; no database.
- **Frontend:** plain HTML, CSS and JavaScript with no build step. Chart.js is stored locally, so the demo does not need the internet.
- **Data contract:** Pydantic models in `backend/models.py`, exported to `docs/contract.json`.
- **Texts on screen:** `data/reference/messages.json`. Thresholds in the texts are placeholders filled from `ranges.json`,
  so the screen and the rules always use the same numbers.

### Status (2026-11-15)

| Part | Status |
|---|---|
| Data contract (`models.py`, `contract.json`) | done, tested |
| O₂/CO₂ mass balance and countdowns (`atmosphere.py`) | done, tested |
| Reference limits and alert texts (`ranges.json`, `messages.json`) | done |
| DONKI event record (`data/donki/`) | done |
| Simulator, rules, API, dashboard | **[TODO: update before submission]** |
| EVA view, Earth link, profile selector | **[TODO]** |

## Run locally

```bash
pip install -r requirements.txt
python -m pytest -q
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>.

Refresh the DONKI record (optional, needs the internet; uses `DEMO_KEY` unless `NASA_API_KEY` is set; the key is never saved):

```bash
python data/donki/fetch_donki.py --sep-id 2024-05-10T13:35:00-SEP-001
```

## NASA data and sources

Every limit in `data/reference/ranges.json` carries its source and one of three statuses:

- **verified**: the value was read from the source text;
- **partially verified**: the source is verified, but a unit conversion or interpretation is ours;
- **unverified**: a demo parameter or team model with no official source. These are not medical thresholds.

The sources were checked again on 2026-11-14 against NASA-STD-3001 Volume 2 **Revision F** (approved 2026-07-14).

**NASA data and standards used**

- **NASA DONKI** (Space Weather Database Of Notifications, Knowledge, Information), SEP endpoint via [NASA Open APIs](https://api.nasa.gov/).
  Demo event: [2024-05-10T13:35:00-SEP-001](https://ccmc.gsfc.nasa.gov/DONKI/view/SEP/30677/-1) (GOES-P: SEISS >10 MeV).
  DONKI gives the event time and type, **not** dose. The dose on screen is simulated and labeled.
- **NASA-STD-3001 Volume 2, Rev F**: [PDF](https://www.nasa.gov/wp-content/uploads/2025/05/nasa-std-3001-vol-2.pdf).
  Used here:
  - inspired O₂ (PIO₂), Table 6.2-1: normoxia 145–155 mmHg, mild hypoxia lower limit 127 mmHg [V2 6003];
  - cabin ppCO₂ ≤ 3 mmHg, 1-hour average [V2 6004];
  - total pressure [V2 6006];
  - suit CO₂ (PICO₂), Table 11.3-1 [V2 11039];
  - suit monitoring [V2 11034]–[V2 11038].

  Web pages: [Section 6](https://www.nasa.gov/reference/6-0-natural-and-induced-environments-vol-2) ·
  [Section 11](https://www.nasa.gov/reference/11-0-spacesuits-vol-2/).
- **NASA-STD-3001 Volume 1**: [V1 4030] 600 mSv career limit; [V1 4031] 250 mSv design value for a solar particle event.
  We use 250 mSv as an alert threshold, not as an astronaut limit.
- **NASA OCHMO-TB-004 Carbon Dioxide (Rev D)**: [PDF](https://www.nasa.gov/wp-content/uploads/2023/12/ochmo-tb-004-carbon-dioxide.pdf).
  Used here:
  - CO₂ symptom ranges;
  - the 2.5 mmHg 7-day average for headache risk under 1%;
  - metabolic O₂/CO₂ rates (Human Integration Design Handbook, 2014).
- **MSL RAD (Curiosity)**: about 1.8 mSv/day in cruise. Sources: Zeitlin et al., *Science* 340 (2013), [doi:10.1126/science.1235989](https://doi.org/10.1126/science.1235989);
  Hassler et al., *Science* 343 (2014), [doi:10.1126/science.1244797](https://doi.org/10.1126/science.1244797);
  [SwRI press release](https://www.swri.org/newsroom/press-releases/swri-led-team-calculates-the-radiation-exposure-associated-trip-mars).

**Other references**

- 30 CFR 7.506, refuge alternative breathable air: [eCFR](https://www.ecfr.gov/current/title-30/section-7.506).
- National Research Council (2007), *Emergency and Continuous Exposure Guidance Levels for Selected Submarine Contaminants: Volume 1*,
  Table S-1: [NAP](https://www.nap.edu/read/11170/chapter/2).
- Submarine CO₂ exposure guideline study (2018): [PubMed 30511437](https://pubmed.ncbi.nlm.nih.gov/30511437/).
- MDN, SpeechRecognition (voice interface, optional): [MDN](https://developer.mozilla.org/docs/Web/API/SpeechRecognition).
- Chart.js (bundled locally, MIT license).

## Limitations

- Simulated data only; there are no real sensors. Not a medical device and not a diagnosis.
- Vital-sign deviation thresholds, the 3-consecutive-readings rule and the 80% "Watch" tier are team choices (unverified).
- The radiation dose is a model: a 1.8 mSv/day base rate plus a simulated event.
- CO and methane are not modeled in the refuge chamber profile.
- Out of scope: bone density, immune markers and behavioral health.

## Use of AI (SI)

We used **Claude Code (Anthropic)** for the following:

- writing backend and frontend code from our specification;
- drafting the alert texts, this README and the AI use declaration;
- checking source links and values.

Team members reviewed and tested every change through pull requests. Each team member can explain the code they approved.

AI did **not**:

- choose the project idea;
- choose the thresholds (they come from the sources listed above, or are marked as team assumptions);
- generate the space weather data (it is real NASA DONKI data).

Where it is marked:

- [docs/AI_USE_DECLARATION.md](docs/AI_USE_DECLARATION.md);
- the `meta.si_kullanimi` field in `ranges.json` and `messages.json`;
- `Co-Authored-By: Claude` lines in commits.

No AI-generated images, video or audio are used **[TODO: confirm before submission]**.

## Team

**[TODO: team name]** · Aydın (backend & frontend), Barış (validation & Earth link), Efe (reference data & delivery)

Team working rules and branch model: [AGENTS.md](AGENTS.md). Some internal planning documents in `docs/` and `reports/` are in Turkish.
