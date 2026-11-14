# AI Use Declaration (DRAFT)

> Draft, 2026-11-14. Check against the official Space Apps submission form before sending.
> Items marked **[TODO]** must be confirmed by the team.

## Summary

Our team used generative AI tools during the hackathon. All AI output was reviewed by a team member,
and every piece of code in the repository can be explained in one sentence by the person who approved it.
All data shown in the app is simulated. The app is decision support, not medical diagnosis.

## Tools used

| Tool | Used for |
|---|---|
| Claude Code (Anthropic) | Generating backend (FastAPI, simulator, O₂/CO₂ model, rules) and frontend code from our specification; running tests |
| Claude Code (Anthropic) | Drafting user-facing alert texts (`data/reference/messages.json`), this declaration and README drafts |
| **[TODO]** other tools (e.g. Gemini, Codex, image/video, translation) | |

## What AI did NOT do

- **Thresholds and limits were not chosen by AI.** Every limit in `data/reference/ranges.json` was read by the team
  from the source (NASA-STD-3001 Vol 1 & 2, OCHMO-TB-004, 30 CFR 7.506, MSL RAD) and tagged
  verified, partially verified or unverified. Unverified values are demo parameters, not medical thresholds.
- **Space weather data is real NASA data, not AI-generated.** The solar particle event comes from the NASA DONKI API
  (stored as JSON before the demo). Radiation dose is a simulation and is labeled as such.
- **Project idea, scope, priorities and demo scenario** were decided by the team.

## How we checked AI output

- Code: a team member read and tested the generated backend with pytest
  (consecutive readings, missing data never counted as normal, threshold edges, exercise not flagged as cardiac load,
  radiation warning before dose is received). Every change goes through a pull request with scope, lint and test checks.
- Text: every alert text uses placeholders filled from `ranges.json`, so the numbers on screen come from the
  verified table, not from AI-written text.
- Sources: every source link in the slides was opened and checked by hand **[TODO: confirm done]**.

## Images, video and voice

**[TODO]** State whether any image, video, music or voice in the submission was AI-generated.
If none: "No AI-generated images, video, audio or voice were used."
