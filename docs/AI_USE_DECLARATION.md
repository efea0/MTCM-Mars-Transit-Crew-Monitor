# AI Use Declaration

> Updated 2026-11-15. Space Apps calls AI tools "Super Intelligence (SI)". This text answers the project form field
> "Use of Super Intelligence (SI)".

## Space Apps SI rules we follow

From the official Project Submission Guide (2026-11-13):

- SI tools are allowed, but SI must not come up with the idea for solving the challenge.
- Code and data made with SI must say so in descriptive text and in metadata.
- SI-generated images and video need a visible watermark. SI-generated audio must be stated in text and metadata.
- SI-generated content must not use or change NASA branding (logos, flags, mission identifiers).

## Where SI use is marked in this project

- This file and the "Use of AI (SI)" section of `README.md`.
- Early commits written with Claude Code carry a `Co-Authored-By: Claude` line; later AI-assisted changes are tracked in pull requests and in this declaration.
- `meta.si_kullanimi` field in `data/reference/ranges.json` and `data/reference/messages.json`.

## Summary

Our team (**Los Galacticos**: Aydın — backend & frontend, Barış — validation & Earth link, Enis / Efe — reference data & delivery) used generative AI tools during the hackathon. All AI output was reviewed by a team member,
and every piece of code in the repository can be explained in one sentence by the person who approved it.
All data shown in the app is simulated. The app is decision support, not medical diagnosis.

## Tools used

| Tool | Used for |
|---|---|
| Claude Code (Anthropic) | Generating backend (FastAPI, simulator, O₂/CO₂ model, rules) and frontend code from our specification; running tests |
| Claude Code (Anthropic) | Drafting user-facing alert texts (`data/reference/messages.json`), this declaration and README drafts |
| Claude Code (Anthropic) | Checking source links and values in `ranges.json` and the project draft against the cited documents |
| Codex (OpenAI) | Limited use; small share of the code |
| Antigravity | Limited use; small share of the code |
| opencode with DeepSeek, GLM and Mistral models | Very limited use; small share of the code |
| Claude Code (Anthropic) | Scripting the demo video (see below) |

## What AI did NOT do

- **Thresholds and limits were not chosen by AI**. Every entry in
  `data/reference/ranges.json` has a source and a status tag:
  - *verified* and *partially verified* entries were read by the team from the source
    (NASA-STD-3001 Vol 1 & 2, OCHMO-TB-004, 30 CFR 7.506, MSL RAD);
  - *unverified* entries (source starts with "assumption") have no official source. The team chose them as demo
    parameters (for example vital-sign deviation thresholds, 3 consecutive readings, the 80% yellow tier).
    They are not medical thresholds and are marked as assumptions in `ranges.json` and in the alert texts' source lists.
- **Space weather data is real NASA data, not AI-generated.** The solar particle event comes from the NASA DONKI API
  (stored as JSON before the demo). Radiation dose is a simulation and is labeled as such.
- **Project idea, scope, priorities and demo scenario** were decided by the team.

## How we checked AI output

- Code: a team member read the generated code and tested it with pytest. Every change goes through a pull request
  with scope, lint and test checks. Tests in the repository today cover:
  - the data contract: missing data is never counted as normal, every alert needs evidence, exactly 4 crew;
  - the O₂/CO₂ model: countdowns, strict limit edges, O₂ supply failure, exercise raising O₂ use,
    and thresholds read from `ranges.json` instead of being written in code;
  - the demo scenarios (`tests/test_scenarios.py`): each scenario's expected-decision table is recomputed with an
    independent model and checked for these behaviors: an alert needs 3 consecutive readings; exercise never raises a
    cardiac-load alert; when activity is unknown, heart rate is not used as evidence; the radiation warning comes
    before the dose is received; missing data is "unknown", never "normal". Thresholds must match `ranges.json`,
    and the solar storm must match the saved NASA DONKI event;
  - the Earth link delay queue (`backend/comms.py`), with the 3–22 min delay read from `ranges.json`;
  - the DONKI fetch script (`data/donki/fetch_donki.py`).

  Two known gaps in the data contract are recorded as expected failures (`xfail`) and are not counted above.
  The engine is also tested on the same scenarios: `test_engine_alerts_fall_inside_expected_windows` and
  `test_engine_end_status_matches_expected` in `tests/test_scenarios.py` run `backend/rules.py` on hypoxia,
  hypercapnia, solar_storm, sensor_dropout and sleep_deprivation and check alert times and end status.
- Text: live alert templates are written in `backend/rules.py`, and the numeric thresholds and limits they display are loaded
  from `data/reference/ranges.json` at runtime, so the limits on screen come from the same table the rules use.
  `data/reference/messages.json` holds the reviewed text drafts with their sources (not read at runtime).
  A few context words stay fixed in the text: averaging periods (1-hour, 7-day), "headache risk under 1%",
  and the submarine profile note (0.8% CO₂ for 90-day exposure).
- Sources: on 2026-11-14 the source links in `ranges.json` and the project draft were opened with Claude Code's help
  and compared with our values (NASA-STD-3001 Vol 2 Rev F, Rev B Table 1 / NTRS 20220019130, OCHMO-TB-004 Rev D,
  30 CFR 7.506, NRC 2007, PubMed 30511437, SwRI/MSL RAD, NASA DONKI). Mismatches were fixed and their status was
  lowered to *partially verified*. The final slides are checked the same way.

## Images, video and voice

The Space Apps **demo video is AI-assisted**: its motion graphics are written as code with HyperFrames and scripted with Claude Code,
and the video carries a visible SI watermark in the bottom-right corner. It uses no NASA logos, flags or mission identifiers.
The crew panels in it are motion graphics drawn after the app's screens and scenarios (labeled "SIMULATED DATA"); it is not a screen recording.
The video has no audio, so no AI-generated audio or voice is used. No other AI-generated images, video, audio or voice were used.
