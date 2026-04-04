# Repo Improvements

This branch focuses on pipeline and repo improvements, excluding UI/backend hardening.

## Implemented Areas

- Align docs and runtime defaults so the repo describes the system that actually runs.
- Ignore generated root-level output by default to keep the worktree clean.
- Add a normalized graph contract on top of parser-native node and edge labels.
- Improve indexing so one run can include all supported languages detected in a repo.
- Select prompt guidance by repo archetype instead of assuming every repo is a backend monolith.
- Validate mandatory agent artifacts and record pipeline metadata more explicitly.
- Add automated tests around config loading, indexer detection, prompt selection, and artifact validation.

## Out Of Scope In This Branch

- UI/backend session isolation, query timeouts, and related hardening in `ui/server/`.

## Why These Changes Matter

- The repo already has a good product shape, but reliability depended too heavily on conventions.
- The graph schema was shared across languages but not consistently normalized for cross-language tooling.
- Prompt guidance was too backend-biased for JavaScript/frontend or library repositories.
- Generated artifacts and drifting defaults weakened trust in the project.

## Follow-On Work

- Expand normalized query coverage across more toolkit methods.
- Add richer mixed-language fixture repos for end-to-end validation.
- Revisit deeper semantic cross-language linking once multi-language indexing is stable.
