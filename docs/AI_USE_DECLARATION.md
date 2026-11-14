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

- **Thresholds and limits were not chosen by AI** **[TODO: team confirms]**. Every entry in
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
    and thresholds read from `ranges.json` instead of being written in code.

  **[TODO before submitting]** Alert rules (`backend/rules.py`) are not written yet. List rule tests here only after
  they exist (for example: consecutive readings, exercise not flagged as cardiac load,
  radiation warning before the dose is received).
- Text: live values and most thresholds in alert texts are placeholders filled from `ranges.json` at runtime.
  Some fixed reference numbers are written directly in the text, for example the 600 mSv career limit,
  the 250 mSv solar-event design value, suit CO₂ 15 mmHg, 14 h suit time, ppCO₂ 2.5 mmHg and 2.8–4.5 mmHg,
  and the 3–22 min Mars–Earth delay. Each text lists its source next to it.
  **[TODO]** Confirm every fixed number against its source, or turn it into a placeholder
  (`data/reference/messages.json`).
- Sources: every source link in the slides was opened and checked by hand **[TODO: confirm done]**.

## Images, video and voice

**[TODO]** State whether any image, video, music or voice in the submission was AI-generated.
If none: "No AI-generated images, video, audio or voice were used."
