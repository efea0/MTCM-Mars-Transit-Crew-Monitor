# Demo script

Two versions use the same five scenarios, in the same order:
**nominal → hypoxia → hypercapnia → solar_storm → sensor_dropout**.

- **Part 1:** the Space Apps "Project Demo" video. It must be **30 seconds or less**.
- **Part 2:** the live presentation, about 3 minutes.

All times below were measured on 2026-11-15 by running the backend pipeline (`backend.main.Pipeline`) on `develop`
for every scenario at the default speed of **×600**.
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
| 3–7 s | nominal | 6–9 s | Four crew cards are Normal. Neil is exercising: his heart rate rises, but his card stays **Normal**. | "Exercise is not an alarm." |
| 7–15 s | hypoxia | 60–68 s | O₂ generation has failed and the **O₂ countdown** is running. Katherine is exercising: **Watch** at 65 s, then **Action** at 66.5 s. Open **Why?** to show SpO₂ below her baseline, plus cabin PIO₂ < 145. | "Per person: Katherine's SpO₂ drops 6 below *her* baseline. Act now." |
| 15–20 s | hypercapnia | 28–31 s, then 56–60 s | Cabin ppCO₂ passes 2.5 (**Watch**). Cut to 56–60 s: the 1-hour average passes 3.0 and Katherine turns **Action** (CO₂ effect). | "CO₂ removal at 40%: the cabin, then the person." |
| 20–25 s | solar_storm | 2–6 s | The solar event starts at 3 s. Every card turns **Action**: "go to the storm shelter" before the dose arrives (projected 300 mSv ≥ 200 mSv). | "Real NASA DONKI event (10 May 2024). Warned *before* the dose." |
| 25–29 s | sensor_dropout | 3–6 s, then 7–8.5 s | Sally's heart-rate sensor goes silent, and Sally's card shows **NO DATA**, not Normal. Cut to John: a **stale** SpO₂ reading is not treated as valid. | "Missing data is never normal." |
| 29–30 s | End card | none | Repo link and "Simulated data · Decision support, not diagnosis". | none |

The DONKI event name is not shown on the panel yet (see "Open points"), so the 20–25 s caption carries it.

---

## Part 2: live presentation (~3 min)

Start each scenario from the demo panel. Open the live link with `?demo=1` at the end to show the panel, and type the demo key
into its **Demo key** field. The key is set on Render and is not stored in the repo.

The panel buttons use these labels:

| Scenario | Button |
|---|---|
| `nominal` | Nominal cruise |
| `hypoxia` | O₂ failure |
| `hypercapnia` | CO₂ rise |
| `solar_storm` | Solar storm |
| `sensor_dropout` | Sensor dropout |
Before the demo, open the live link once, because the Render service takes about 1 minute to wake.

### 1. nominal (about 20 s; the scenario lasts 18 s)

| Real s | Do / show | Say |
|---|---|---|
| 0 | Start `nominal`. Point at the four crew cards, the cabin O₂/CO₂ and the two countdowns. | "Four astronauts on the way to Mars. Each one is compared to their own baseline." |
| 6–9 | Neil starts exercising and his heart rate rises toward 125. | "Neil is exercising. His heart rate goes up, and the system does not raise an alarm." |
| 18 | The scenario ends. Everyone is still Normal. | "No false alarms." |

### 2. hypoxia (about 75 s; the scenario lasts 180 s, stop after the Action step)

| Real s | Do / show | Say |
|---|---|---|
| 0–1 | Start `hypoxia`. O₂ generation fails at 1 s. | "The O₂ generator stops." |
| 2–15 | The **O₂ countdown** starts. The CO₂ countdown does not move. | "Two separate countdowns: O₂ is running out, CO₂ removal still works." |
| 17–21 | Neil turns **Watch** (cardiac load). John has the same heart rate (80) and stays **Normal**. | "Same heart rate, different decision: Neil is +16 over his baseline, John only +13." |
| 37–40 | Cabin PIO₂ drops below 145 and the cabin turns **Watch**. | "Cabin PIO₂ is now below normoxia." |
| 60 | Katherine starts exercising (heart rate 130). | "Katherine starts exercising. Her high heart rate is not counted as evidence." |
| 65 | Katherine turns **Watch** (possible hypoxia: SpO₂ about 4 below her baseline). | none |
| 66.5 | Katherine turns **Action**. Click **Why?** | "Her SpO₂ is 6 below her own baseline and cabin PIO₂ is below 145. Advice: stop exercising, switch to backup O₂. This comes long before the cabin alarm turns red." |
| 70 | Move on before 71 s: at 71 s Katherine stops exercising, and at 74.5 s her card goes back to **Watch**. Optional: say "the cabin alarm turns red at 177 s" without waiting for it. | none |

### 3. hypercapnia (about 60 s; the scenario lasts 67 s)

| Real s | Do / show | Say |
|---|---|---|
| 0–1 | Start `hypercapnia`. CO₂ removal drops to 40%. | "Now CO₂ removal partly fails." |
| 28–31 | Cabin ppCO₂ passes 2.5 mmHg and the cabin turns **Watch**. | "First a cabin warning." |
| 50–53 | Katherine's breathing rises (+6 over her baseline) and she turns **Watch** (CO₂ effect). Open **Why?** | "Then only the person who shows symptoms: Katherine is breathing faster." |
| 56–60 | The 1-hour average passes 3.0 mmHg. The cabin and Katherine turn **Action**. | "The 1-hour average passes NASA's 3 mmHg limit." |

### 4. solar_storm (about 15 s; the scenario lasts 78 s, stop after the shelter step)

| Real s | Do / show | Say |
|---|---|---|
| 0 | Start `solar_storm`. | "This uses the time and type of a real NASA DONKI solar particle event from 10 May 2024. The dose is simulated." |
| 3–4 | The event starts at 3 s. At 4 s all four turn **Action**: "go to the storm shelter". Open **Why?** to show the projected dose of 300 mSv, which is at least 200 mSv. | "The system projects the end-of-event dose and warns *before* the crew receives it." |
| 4.5 | The crew is in the shelter, and the dose rate drops to a quarter. | none |
| 7 | The cards go back to **Normal** after the shelter move. Stop here. | none |

### 5. sensor_dropout (about 15 s; the scenario lasts 12 s)

| Real s | Do / show | Say |
|---|---|---|
| 0 | Start `sensor_dropout`. | "Last: sensors fail." |
| 3–6 | At 3 s Sally's heart-rate sensor stops: the reading shows **NO DATA** and Sally's card is not Normal (from 4 s to 5.5 s it is **Watch**, "no data from heart_rate sensor"). At 5 s Katherine turns **Watch** (resting heart rate about +22 over her baseline). | "A missing reading is never normal, and one broken sensor does not hide someone else's alert." |
| 7–8.5 | John's SpO₂ sensor repeats an old value (97, **stale**). From 8 s John is **Watch** for missing SpO₂. | "A stale value is not counted." |
| 10–11.5 | The cabin CO₂ sensor reports an impossible value (12 mmHg, **sensor fault**). It does not trigger the CO₂ limit alarm; at 11 s the cabin shows a sensor alert ("no data from pp_co2 sensor") instead. | "A faulty value is not used against the limit." |

Close: "All data is simulated. This is decision support, not diagnosis."

---

## Open points (for Aydın)

1. **DONKI event on screen.** The frame carries no DONKI event type or time, so `messages.json` → `why_note_event`
   (`{event_type}`, `{event_time}`) cannot be filled. The panel shows the dose, but not *which* real event it is.
   The backend now serves the record at `/api/sep-event`, but the panel does not show it yet.
   Until this is fixed, the video caption and the presenter carry the DONKI name.
2. **Not in this demo:** the EVA view, the profile selector and the voice interface are planned, not built yet. The Earth link panel exists
   but is not part of this script.
3. Time windows can move by one reading (0.5 s) because of the 3-consecutive-reading rule. Record a little longer than each window.
