---
name: agro-feature
description: Run the Agrolytics feature pipeline end to end — plan, implement, test, review — by chaining the agro-planner, agro-builder, agro-tester and agro-reviewer subagents with a user gate after the plan. Use when the user asks to build a feature or fix a non-trivial bug in this repo, or types /agro-feature.
---

# Pipeline de feature Agrolytics

Chain: `agro-planner` → **user gate** → `agro-builder` → `agro-tester` → `agro-reviewer`.

The argument to this skill is the feature request or bug report. If it is missing, ask for it before doing anything else.

## 1. Locate (optional)

If the request names code you cannot place, spawn `cavecrew-investigator` first with a narrow question ("where is pest risk raster generated", "what calls plans.py"). Its output is compressed, so it costs less context than searching inline. Skip this step when the target is already obvious.

## 2. Plan

Spawn `agro-planner` with the request plus anything the investigator returned. It is read-only and returns a file-level plan, migration decision, test list, and risks.

## 3. User gate — do not skip

Show the plan to the user and stop. Ask for approval before any code is written.

Exception: skip the gate only if the user explicitly said to run the whole pipeline unattended (e.g. "no me preguntes", "corré todo"). Any migration in the plan, any change to `entrypoint.sh` / `render*.yaml` / `fly.toml` / Dockerfile, or anything touching auth or billing **always** gets the gate, regardless of what the user said — those have taken prod down in this repo before.

## 4. Build

Spawn `agro-builder` with the approved plan verbatim. It implements and runs `ruff format`, `ruff check --fix`, and `mypy` on the files it changed.

If the builder reports items under **Pendiente** or **Fuera de plan**, surface them to the user before moving on. Do not paper over them.

## 5. Test

Spawn `agro-tester`. It adds tests, runs the suite, and fixes red.

If it reports remaining failures, do not proceed to review. Report the failures to the user and ask how to continue — a red suite is a stop condition, not a warning.

## 6. Review

Spawn `agro-reviewer` on the resulting diff. It reports findings against Agrolytics's production invariants and never edits.

Findings marked `crítico` or `alto` go back to `agro-builder` for a fix round, then `agro-tester` re-runs. Repeat at most twice; if findings survive two rounds, stop and hand it to the user with the open items listed.

## 7. Report

Summarise for the user:

```
Plan:     <one line>
Archivos: N modificados, M nuevos
Migración: revision id | ninguna
Tests:    pytest -q -> N passed
Tipos:    mypy (archivos tocados) -> limpio | N errores
Review:   X hallazgos (crítico/alto/medio) | Sin hallazgos
Pendiente: what still needs a human
```

Never commit or push at the end of the pipeline. Ask first — commits are the user's call.

## Notes

- Run the steps in order. The stages are dependent, so nothing here parallelises.
- The `agro-*` subagents carry Agrolytics domain rules (layer map, manual alembic revision ids, plan gating). The `cavecrew-*` subagents are generic and compression-focused — use `cavecrew-investigator` for lookups and `cavecrew-builder` only for a bounded 1–2 file tweak that does not need a plan.
