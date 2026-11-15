# Demo script

Two versions use the same five scenarios, in the same order:
**nominal → hypoxia → hypercapnia → solar_storm → sensor_dropout**.

- **Part 1:** the Space Apps "Project Demo" video. It must be **30 seconds or less**.
- **Part 2:** the live presentation, about 3 minutes.

All times below were calculated from `data/scenarios/*.json` at the default speed of **×600**.
At that speed 1 mission minute = 0.1 s, and one reading (every 5 mission minutes) = 0.5 s.
"Real s" is the time since the scenario was started from the demo panel. Use it to know when to record or when to speak.
Labels follow the English panel. If a label changes in the final UI, keep the step and change only the label.

Rules for the video (Space Apps guide): English only, ≤ 30 s, a visible **SI watermark** (motion graphics are AI-assisted),
no NASA logos, flags or mission identifiers, and the "SIMULATED DATA" badge stays visible in every shot.

---

## Part 1: 30-second video

Record each scenario as its own screen recording at ×600, then cut the windows below. Each caption is one short line.

| Video time | Scenario | Record at (real s) | What is on screen | Caption |
|---|---|---|---|---|
| 0–3 s | Title | none | App name and the challenge title. SI watermark on. | "Crew health + cabin air, in one model." |
| 3–7 s | nominal | 6–9 s | Four crew cards are Normal. Ayşe is exercising: her heart rate rises, but her card stays **Normal**. | "Exercise is not an alarm." |
| 7–15 s | hypoxia | 60–67 s | O₂ generation has failed and the **O₂ countdown** is running. Ece is exercising: **Watch** at 62–64 s, then **Action** at 63–66 s. Open **Why?** to show SpO₂ below her baseline, plus cabin PIO₂ < 145. | "Per person: Ece's SpO₂ drops 6 below *her* baseline. Act now." |
| 15–20 s | hypercapnia | 28–31 s, then 56–60 s | Cabin ppCO₂ passes 2.5 (**Watch**). Cut to 56–60 s: the 1-hour average passes 3.0 and Ece turns **Action** (CO₂ effect). | "CO₂ removal at 40%: the cabin, then the person." |
| 20–25 s | solar_storm | 2–6 s | The solar event starts at 3 s. Every card turns **Action**: "go to the storm shelter" before the dose arrives (projected 300 mSv ≥ 200 mSv). | "Real NASA DONKI event (10 May 2024). Warned *before* the dose." |
| 25–29 s | sensor_dropout | 4–6 s, then 8–9.5 s | Can's heart-rate sensor goes silent, and his card shows **NO DATA**, not Normal. Cut to Mert: a **stale** SpO₂ reading is not treated as valid. | "Missing data is never normal." |
| 29–30 s | End card | none | Repo link and "Simulated data · Decision support, not diagnosis". | none |

The DONKI event name is not shown on the panel yet (see "Open points"), so the 20–25 s caption carries it.

---

## Part 2: live presentation (~3 min)

Start each scenario from the demo panel. You need the demo key, which is set on Render and is not stored in the repo.
Before the demo, open the live link once, because the Render service takes about 1 minute to wake.

### 1. nominal (about 20 s; the scenario lasts 18 s)

| Real s | Do / show | Say |
|---|---|---|
| 0 | Start `nominal`. Point at the four crew cards, the cabin O₂/CO₂ and the two countdowns. | "Four astronauts on the way to Mars. Each one is compared to their own baseline." |
| 6–9 | Ayşe starts exercising and her heart rate rises toward 125. | "Ayşe is exercising. Her heart rate goes up, and the system does not raise an alarm." |
| 18 | The scenario ends. Everyone is still Normal. | "No false alarms." |

### 2. hypoxia (about 75 s; the scenario lasts 180 s, stop after the Action step)

| Real s | Do / show | Say |
|---|---|---|
| 0–1 | Start `hypoxia`. O₂ generation fails at 1 s. | "The O₂ generator stops." |
| 2–15 | The **O₂ countdown** starts. The CO₂ countdown does not move. | "Two separate countdowns: O₂ is running out, CO₂ removal still works." |
| 17–21 | Ayşe turns **Watch** (cardiac load). Mert has the same heart rate (80) and stays **Normal**. | "Same heart rate, different decision: Ayşe is +16 over her baseline, Mert only +13." |
| 37–40 | Cabin PIO₂ drops below 145 and the cabin turns **Watch**. | "Cabin PIO₂ is now below normoxia." |
| 60 | Ece starts exercising (heart rate 130). | "Ece starts exercising. Her high heart rate is not counted as evidence." |
| 62–64 | Ece turns **Watch** (possible hypoxia). | none |
| 63–66 | Ece turns **Action**. Click **Why?** | "Her SpO₂ is 6 below her own baseline and cabin PIO₂ is below 145. Advice: stop exercising, switch to backup O₂. This comes long before the cabin alarm turns red." |
| 70 | Move on. Optional: say "the cabin alarm turns red at 177 s" without waiting for it. | none |

### 3. hypercapnia (about 60 s; the scenario lasts 67 s)

| Real s | Do / show | Say |
|---|---|---|
| 0–1 | Start `hypercapnia`. CO₂ removal drops to 40%. | "Now CO₂ removal partly fails." |
| 28–31 | Cabin ppCO₂ passes 2.5 mmHg and the cabin turns **Watch**. | "First a cabin warning." |
| 50–53 | Ece's breathing rises (+6 over her baseline) and she turns **Watch** (CO₂ effect). Open **Why?** | "Then only the person who shows symptoms: Ece is breathing faster." |
| 56–60 | The 1-hour average passes 3.0 mmHg. The cabin and Ece turn **Action**. | "The 1-hour average passes NASA's 3 mmHg limit." |

### 4. solar_storm (about 15 s; the scenario lasts 78 s, stop after the shelter step)

| Real s | Do / show | Say |
|---|---|---|
| 0 | Start `solar_storm`. | "This uses the time and type of a real NASA DONKI solar particle event from 10 May 2024. The dose is simulated." |
| 3–4.5 | The event starts. All four turn **Action**: "go to the storm shelter". Open **Why?** to show the projected dose of 300 mSv, which is at least 200 mSv. | "The system projects the end-of-event dose and warns *before* the crew receives it." |
| 4.5 | The crew is in the shelter, and the dose rate drops to a quarter. | none |

### 5. sensor_dropout (about 15 s; the scenario lasts 12 s)

| Real s | Do / show | Say |
|---|---|---|
| 0 | Start `sensor_dropout`. | "Last: sensors fail." |
| 3–6 | Ece turns **Watch** (heart rate +22). At the same time, Can's heart-rate sensor stops and his card shows **NO DATA**, not Normal (4–5.5 s). | "A missing reading is never normal, and one broken sensor does not hide someone else's alert." |
| 8–9.5 | Mert's SpO₂ sensor repeats an old value (**stale**). | "A stale value is not counted." |
| 11–12 | The cabin CO₂ sensor reports an impossible value (**sensor fault**). It does not trigger a CO₂ alarm. | "A faulty value is not used against the limit." |

Close: "All data is simulated. This is decision support, not diagnosis."

---

## Open points (for Aydın)

1. **DONKI event on screen.** The frame carries no DONKI event type or time, so `messages.json` → `why_note_event`
   (`{event_type}`, `{event_time}`) cannot be filled. The panel shows the dose, but not *which* real event it is.
   Until this is fixed, the video caption and the presenter carry the DONKI name.
2. **Not in this demo:** the EVA view, the Earth panel and the profile selector are not built yet.
   README "Demo walkthrough" steps 5–7 still describe them, so update them or mark them as planned.
3. Time windows can move by one reading (0.5 s) because of the 3-consecutive-reading rule. Record a little longer than each window.
