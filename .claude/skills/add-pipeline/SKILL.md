---
name: add-pipeline
description: Add a new lumen pipeline (a new `lumen <name>` CLI command with its own fan-out/fan-in agent stage, reusing the existing preflight/indexer). Use when the user wants to add a new pipeline, a new agent-stage command, or an alternative to `lumen run`/`lumen security-audit`.
---

# Add a new lumen pipeline

This is a thin wrapper. All of the actual instructions — the mental model, reusable
building blocks, naming conventions, copy-paste module skeletons, make/Docker/native
checklist, and verification steps — live in `docs/adding-a-pipeline.md` at the repo root.
Do not duplicate that content here or let it drift out of sync; always read the file fresh.

## What to do

1. Read `docs/adding-a-pipeline.md` in full.
2. If the user's request doesn't already answer every field in that document's
   "Your task" section (pipeline name, what it analyzes/produces, fan-out roles, fan-in
   output, whether it needs an MkDocs build step, which graph tools each role should lean
   on), ask them via `AskUserQuestion` before writing any code. Don't guess at the pipeline
   name or role split — a bad guess here produces a structurally inconsistent pipeline.
3. Implement every step in the document's "Step-by-step" section, in order, using its
   naming-conventions table exactly (kebab-case pipeline name → snake_case module names →
   `<short-name>/<role>` phase labels and artifact paths, etc.).
4. Follow the document's "What NOT to touch" list strictly — never modify
   `stages/agent.py`, `archetype_registry.py`, `artifact_planner.py`, `preflight/*`,
   `stages/indexer.py`, `Dockerfile`, or `scripts/build-native.sh` to add a pipeline.
5. Run the document's "Verify" checklist before considering the work done, including a
   smoke test against a small synthetic repo (not a large real one — keep token/time cost
   low) that confirms the fan-out roles actually ran concurrently and the fan-in step
   produced the final artifact. Clean up smoke-test output before finishing.
6. If, while implementing, you find the document itself is unclear, missing a step, or the
   reference implementation (`security-audit`) has moved/changed, fix
   `docs/adding-a-pipeline.md` too — it must stay accurate for the next person.
