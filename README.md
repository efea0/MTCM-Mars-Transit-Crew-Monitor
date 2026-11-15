# MTCM: Mars Transit Crew Monitor

**NASA Space Apps Challenge 2026** · Challenge: *Create Health Monitoring Software for Astronauts on Space Missions*

A decision-support dashboard for a 4-person crew on the way to Mars. It reads each astronaut's vital signs
together with the cabin air (O₂, CO₂) and radiation, and tells **which crew member** needs to act, **why**, and **what to do**.
Earth is up to 22 minutes away one way, so the crew cannot wait for an answer.

> **All health and dose data in this app is simulated.** The solar event time and type come from a real NASA DONKI record.
> This is **decision support, not medical diagnosis.** Vital-sign deviation thresholds are demo parameters, not medical thresholds.

## Links

| What | Link |
|---|---|
| Live demo | <https://mars-transit-crew-monitor.onrender.com> (Render free tier; first load may take a moment) |
| Project demo (≤ 30 s video or ≤ 7 slides) | [Watch 30 s Demo Video on YouTube](https://youtu.be/5M8-BdbDQKI?si=-AQ-NJC-BS6XMDZP) |
| Space Apps project page | <https://www.spaceappschallenge.org/2026/find-a-team/los_-_galacticos/?tab=details> |
| Source code | <https://github.com/efea0/space-apps-2026-health-monitoring> |

### 30-Second Project Demo

[![Watch 30 s Demo Video on YouTube](https://img.youtube.com/vi/5M8-BdbDQKI/hqdefault.jpg)](https://youtu.be/5M8-BdbDQKI?si=-AQ-NJC-BS6XMDZP)

[Watch 30 s Demo Video on YouTube](https://youtu.be/5M8-BdbDQKI?si=-AQ-NJC-BS6XMDZP)

> **Note:** The free Render tier sleeps after 15 minutes without traffic. `.github/workflows/keepalive.yml` pings `/api/health` periodically from `main`, though GitHub Actions cron runs may be delayed; if the service is asleep, the first load takes about 1 minute.

## What it does

- **Per-person health status.** Heart rate, SpO₂, body temperature and breathing rate for 4 astronauts
  (Neil Armsted, Sally Ryder, Katherine Johnston, John Glennard). Each one is read
  against that person's own baseline: the same heart rate can be normal for one person and a warning for another.
  Status: **Normal / Watch / Action**.
- **Body + cabin in one model.** O₂ use is estimated from each person's heart rate, and that feeds the cabin O₂/CO₂ mass balance.
  Example: *"Katherine: stop exercising, SpO₂ is dropping and cabin PIO₂ is below normoxia."*
- **Two countdowns.** Hours until O₂ reaches its lower limit and until CO₂ reaches its upper limit.
- **"Why?" for every alert.** The evidence behind the decision: the measured value, the threshold and a short note
  (for example how far a vital sign is from that person's baseline, and for how many readings). The source of every
  threshold is listed in `ranges.json` and under "NASA data and sources" below.
- **Exercise does not cause false alarms.** A higher heart rate during exercise is expected and is not flagged as cardiac load.
- **Missing data is never "normal".** A missing sensor shows **NO DATA**.
- **Radiation warning before the dose.** During a solar particle event the system projects the end-of-event dose and
  sends the crew to the storm shelter *before* it is received.
- **Delayed Earth link.** Reports go to Earth with a one-way light delay of 3–22 min in mission time. On the ×600 demo
  clock that is about 0.3–2.2 s.
  The Earth link panel shows each message and the light delay.

**Planned, not built yet:**

- **EVA view** *(planned, not built yet)*: suit PIO₂ and PICO₂, remaining supply time and, if needed, *"return to the airlock"*.
- **Environment (profile) selector** *(planned, not built yet)*: the same O₂/CO₂ engine for a submarine and an underground
  refuge chamber. Their limits and sources are already in `ranges.json`; there is no selector on screen.
- **Voice interface** *(planned, not built yet)*: push-to-talk commands and spoken alerts. The app has no voice input or output today.

## Demo walkthrough

This is the live presentation flow, about 3 minutes. The Space Apps "Project Demo" link is a separate video of 30 seconds or less, or a deck of 7 slides or fewer.
The step-by-step script with timings is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

Scenarios are started from the demo panel. It is hidden until the page is opened with `?demo=1` (for example
<http://127.0.0.1:8000/?demo=1>), and on the live demo it asks for the demo key. Randomness uses a fixed seed, and the
demo needs neither the internet nor voice. The simulation clock runs at ×600; the screen shows the mission elapsed
time, not the speed. A measurement is taken every 5 mission minutes, which is 0.5 s in the demo.

The six scenarios run in this order: **nominal → hypoxia → hypercapnia → solar_storm → sleep_deprivation → sensor_dropout**.

1. **nominal.** The Habitat panel shows four astronauts (Neil, Sally, Katherine, John), cabin O₂ and CO₂, and two countdowns.
   Neil exercises and his heart rate rises to 125, but his card stays **Normal**: exercise is not an alarm.
2. **hypoxia.** O₂ generation fails. Cabin PIO₂ slowly drops and the O₂ countdown starts. CO₂ removal still works, so the CO₂ countdown does not move.
   - **Same heart rate, different decision.** Neil and John both reach a heart rate of 80. Neil is +16 over his baseline and turns **Watch** (cardiac load). John is +13 over his and stays **Normal**.
   - Katherine exercises: heart rate 130, breathing 26/min. Neither is counted as an alarm or as hypoxia evidence. When her SpO₂ is 3 points below her own baseline she turns **Watch**, and at 6 points she turns **Action**. The evidence shown is SpO₂ plus cabin PIO₂, and the advice is *"stop exercise, switch to backup O₂"*. This personal alert comes long before the cabin alert turns red.
3. **hypercapnia.** CO₂ removal drops to 40%. First the cabin turns **Watch** (ppCO₂ above 2.5 mmHg). Then only the person with symptoms, Katherine (breathing +6 over her baseline), turns **Watch**. When the 1-hour average passes 3 mmHg, the cabin and Katherine turn **Action**.
4. **solar_storm.** The real NASA DONKI event (10 May 2024) is replayed. The system projects the end-of-event dose, and when it will pass 200 mSv (80% of the 250 mSv threshold) it says *"Move to storm shelter!"* before the dose is received.
5. **sleep_deprivation.** Sleep length is judged per person across consecutive periods: Sally sleeps below 5 hours twice in a row and turns **Action**, Katherine sleeps below 6 hours twice and turns **Watch**, while a single short sleep by John raises no alert.
6. **sensor_dropout.** Sally's heart-rate sensor stops and Sally's card shows **NO DATA**, not Normal. John's SpO₂ sensor repeats an old value (**stale**), which is not counted. An impossible cabin CO₂ value is marked as a sensor fault and does not trigger a CO₂ alarm.

Planned, not built yet (so not in this demo): the EVA view, the environment (profile) selector and the voice interface.

## How it works

```
data/reference/ranges.json ──┐   (limits + sources + verification status)
data/donki/sep_event.json ───┤   (real NASA DONKI solar particle event)
data/scenarios/*.json ───────┤   (demo scenarios, fixed seed)
                             ▼
backend/simulator.py  →  backend/atmosphere.py  →  backend/rules.py  →  backend/main.py  →  frontend/
(vitals, baseline,        (O₂/CO₂ mass balance,     (per-person +         (FastAPI + SSE)     (Habitat panel,
 circadian rhythm,         two countdowns)            cabin decisions,                         Earth link panel)
 scenario events)                                     3 consecutive readings)
                                                                                 backend/comms.py (delay queue)
```

- **Backend:** Python 3.11+, FastAPI, Server-Sent Events. Data lives in memory and in JSON files; no database.
- **Frontend:** plain HTML, CSS and JavaScript with no build step. Trend charts are drawn as inline SVG and the fonts are stored locally,
  so the demo does not need the internet.
- **API:** `/api/stream` (SSE telemetry), `/api/state`, `/api/history`, `/api/limits`, `/api/sep-event`, `/api/scenarios`
  and `/api/health`. Demo controls (`/api/scenario/{name}`, `/api/control`, `/api/speed`) need the `X-Demo-Key` header
  when `DEMO_CONTROL_KEY` is set on the server.
- **Data contract:** Pydantic models in `backend/models.py`, exported to `docs/contract.json`.
- **Alert texts:** the texts on screen are written in `backend/rules.py`, and the thresholds in them come from `ranges.json`.
  `data/reference/messages.json` holds the reviewed text drafts with their sources; the code does not read it yet.

### Status (2026-11-15)

| Part | Status |
|---|---|
| Data contract (`models.py`, `contract.json`) | done, tested |
| O₂/CO₂ mass balance and countdowns (`atmosphere.py`) | done, tested |
| Reference limits (`ranges.json`) | done, read by the engine |
| Alert text drafts (`messages.json`) | done; not read by the code yet (texts are in `rules.py`) |
| DONKI event record (`data/donki/`) | done |
| Simulator, rules engine, API (SSE), Habitat panel | done, tested |
| Earth link delay queue (`backend/comms.py`) and Earth link panel | done, tested |
| EVA view, profile selector, voice interface | planned, not built yet |

## Run locally

```bash
pip install -r requirements.txt
python -m pytest -q
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>. Add `?demo=1` to show the demo panel. Locally no demo key is needed unless you set
`DEMO_CONTROL_KEY` before starting the server.

Refresh the DONKI record (optional, needs the internet; uses `DEMO_KEY` unless `NASA_API_KEY` is set; the key is never saved):

```bash
python data/donki/fetch_donki.py --sep-id 2024-05-10T13:35:00-SEP-001
```

## NASA data and sources

Every limit in `data/reference/ranges.json` carries its source and one of three statuses:

- **verified**: the value was read from the source text;
- **partially verified**: the source is verified, but a unit conversion or interpretation is ours;
- **unverified**: a demo parameter or team model with no official source. These are not medical thresholds.

The sources were checked again on 2026-11-14 against NASA-STD-3001 Volume 2 **Revision F** (approved 2026-07-14); `ranges.json` also cites **Revision B** Table 1 (presentation `NTRS 20220019130`) for the `PIO₂ = (PB - 47) × FIO₂` formula.

**NASA data and standards used**

- **NASA DONKI** (Space Weather Database Of Notifications, Knowledge, Information), SEP endpoint via [NASA Open APIs](https://api.nasa.gov/).
  Demo event: [2024-05-10T13:35:00-SEP-001](https://ccmc.gsfc.nasa.gov/DONKI/view/SEP/30677/-1) (GOES-P: SEISS >10 MeV).
  DONKI gives the event time and type, **not** dose. The dose on screen is simulated and labeled.
- **NASA-STD-3001 Volume 2, Rev F** ([PDF](https://www.nasa.gov/wp-content/uploads/2025/05/nasa-std-3001-vol-2.pdf)) and **Rev B Table 1** (`NTRS 20220019130` for the `PIO₂` formula).
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
- IBM Plex Sans and IBM Plex Mono fonts (bundled locally, SIL Open Font License; see `frontend/fonts/`).

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
- checking source links and values;
- producing the 30-second demo video (motion graphics code).

Team members reviewed and tested every change through pull requests. Each team member can explain the code they approved.

AI did **not**:

- choose the project idea;
- choose the thresholds (they come from the sources listed above, or are marked as team assumptions);
- generate the space weather data (it is real NASA DONKI data).

Where it is marked:

- [docs/AI_USE_DECLARATION.md](docs/AI_USE_DECLARATION.md);
- the `meta.si_kullanimi` field in `ranges.json` and `messages.json`;
- `Co-Authored-By: Claude` lines in commits.

The Space Apps **demo video is AI-assisted**: its motion graphics are written as code with HyperFrames and scripted with Claude Code,
and the video carries a visible SI watermark in the bottom-right corner. It uses no NASA logos, flags or mission identifiers. The crew panels in it are motion graphics drawn after the app's
screens and scenarios (labeled "SIMULATED DATA"); it is not a screen recording.
No other AI-generated images are used. The video has no audio, so no AI-generated audio is used.

## Team

**Los Galacticos** · Aydın (backend & frontend), Barış (validation & Earth link), Enis / Efe (reference data & delivery)

Team working rules and branch model: [AGENTS.md](AGENTS.md).
`reports/` holds two day-0 research notes written before any code existed; the app as built is described in this README.

## License

Original project contributions: [Apache License 2.0](LICENSE); see also [NOTICE](NOTICE).
Bundled fonts (IBM Plex Sans, IBM Plex Mono): SIL Open Font License 1.1, see `frontend/fonts/`.
NASA DONKI data and NASA-STD-3001 values are public NASA information; this project is not endorsed by NASA.
