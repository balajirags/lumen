# Agent Output Feedback

Date: 2026-04-05

Reviewed outputs:
- `/Users/gbalaji/projects/personal/lumen/output/inventory-service-20260404-184234`
- `/Users/gbalaji/projects/personal/lumen/output/admin-frontend-20260404-190337`

## Summary

The backend run is directionally useful, but diagram rendering and output-format discipline are weak. The frontend run is the bigger problem: although the repo is detected as a frontend app, the generated artifact set and target-state recommendations remain heavily backend-oriented.

## Findings

1. Archetype detection is not changing the artifact contract enough.
   The frontend run is classified as `frontend-app`, but it still emits backend-shaped artifacts such as `domain/er-diagram.md`, `current-state/api-spec.yaml`, and `target-state/strangler-fig.md`.

2. Frontend target-state recommendations invent backend architecture without evidence.
   The frontend target-state outputs introduce service decomposition, REST services, Kafka, PostgreSQL, CDC, and ACL-style migration seams even when the current-state outputs indicate the repo does not contain those backend concerns.

3. Frontend current-state artifacts are internally inconsistent.
   The frontend `api-spec.yaml` states that there are no REST endpoints but still emits an OpenAPI document. The frontend `er-diagram.md` states that no persistent entities were found but is still treated as a normal artifact.

4. Frontend analysis quality is degraded by toolkit/query mismatch.
   The frontend run shows repeated Cypher/Kuzu query errors and too many turns spent on backend-oriented discovery paths instead of frontend-native structure such as routes, components, hooks, module boundaries, and API clients.

5. PlantUML/C4 output is inconsistent and likely responsible for broken diagrams.
   The generated C4 files use raw `@startuml ... @enduml` blocks inconsistently, and some diagrams appear to mix the wrong C4 abstraction level with the selected diagram family.

6. Backend artifact quality is better, but rendering discipline is still weak.
   The backend outputs are broadly aligned with the repo shape, but the PlantUML/C4 formatting contract is not enforced consistently enough to make the diagrams reliable.

## Implications

- Prompt overlays alone are not sufficient; artifact expectations and validation need to vary by archetype.
- Frontend runs need frontend-specific analysis tools and output templates.
- Diagram generation needs a stricter output contract and validation layer.
- Output quality checks should reject obviously contradictory or low-signal artifacts.

## Rectification Themes

1. Split artifact sets by archetype.
2. Make mandatory artifacts archetype-aware.
3. Add frontend-native toolkit queries and prompt examples.
4. Tighten Kuzu-safe query examples and remove backend-first assumptions from shared prompts.
5. Enforce a single PlantUML/C4 formatting contract.
6. Add output validation to catch contradictory or weak artifacts before finalization.
