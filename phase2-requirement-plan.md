# Plan: `lumen ask` + `lumen chat` — Interactive Codebase Q&A

## Context

Lumen currently batch-processes a repo into static artifacts + a docs site. Once the pipeline runs, the knowledge graph (KuzuDB) sits idle at `output/.../index.kuzu/`. This plan adds two interactive modes that let developers query the knowledge graph directly — either for persistent deep-dive artifacts (`ask`) or live exploration while coding (`chat`).

---

## Architecture: Fan-Out + Consensus Critique Per Turn

Both modes share the same underlying 5-step pipeline per question:

```
question
    │
    ▼
[1] Fan-Out: Parallel Researchers (ThreadPoolExecutor)
    - 2–3 researchers run in parallel (domain / flows / tech)
    - Each researcher: run_loop() with KuzuDB tools, 3–5 turns max
    - Each gets own KuzuBackend (thread-safe, existing pattern)
    - Output: specialized factual notes per role
    │
    ▼
[2] Synthesizer Draft (1–2 LLM calls)
    - Combines researcher notes into a first-draft answer
    - Tags claims: [Observed] / [Inferred] / [Hypothesized]
    │
    ▼
[3] Critic Agent (1 LLM call — cheap, targeted)
    - Flags: contradictions between notes, unsupported inferences, gaps, overclaims
    - Output: numbered issues list, or "No issues found"
    - Skipped for focused questions in chat mode (latency matters)
    - Always runs in ask mode (quality > speed for persistent artifacts)
    │
    ▼
[4] Synthesizer Revision (1 LLM call — only if critic found issues)
    - Targeted fixes only — does not rewrite from scratch
    │
    ▼
answer / artifact
```

### Why Fan-Out + Critic (not pure fan-in, not full stochastic consensus)

**vs. pure fan-in:** The synthesizer alone can't detect when Researcher A's inference contradicts Researcher B's finding. The critic catches this in one cheap pass.

**vs. full stochastic consensus (multi-agent debate):** Full debate causes multiplicative context growth — round 2 agents each read all other agents' round 1 outputs (3–5× token blowup). The critic pattern captures ~80% of the quality benefit at ~20–30% extra token cost.

**Key distinction in LLM usage:**
- **Researchers** → `run_loop()` (multi-turn agentic, tool-calling, graph queries)
- **Critic, Synthesizer, Intent Classifier** → `LLMProvider.chat()` (single call, no tools — reason over text only)

---

## Two Modes, One Shared Pipeline

```
lumen ask   →  run pipeline once  →  write .md  →  exit
lumen chat  →  run pipeline per turn in a loop  →  print answer  →  next question
```

### `lumen ask` — Single-Shot, Persistent Artifact

- Question is always self-contained — no anaphora, no history
- No intent classifier — always fans out to all roles
- Critic always runs (quality > speed — it's a permanent artifact)
- Output: writes a `.md` file to `artifacts/qa/<slug>.md` (or `--output` path)
- Use case: "I read the architecture doc, I want a deeper analysis of bounded contexts"

```bash
lumen ask "Explain the payments bounded context in depth" \
  --db output/my-repo-20260403/index.kuzu/my-repo-db
# → writes artifacts/qa/explain-the-payments-bounded-context-in-depth.md

lumen ask "What are the top coupling hotspots?" \
  --db output/my-repo-20260403/index.kuzu/my-repo-db \
  --output output/my-repo-20260403/artifacts/qa/hotspots.md
```

### `lumen chat` — Multi-Turn, Session Memory

- `ChatSession` maintains a `transcript` across the full session
- **Intent classifier** (1 LLM call) sees last 3 turns — resolves anaphora (`"that"`, `"it"`, `"the cache layer"`) into clean sub-questions before handing to researchers
- **Synthesizer** sees last 3 turns — maintains tone/continuity
- **Researchers never see conversation history** — always receive a clean, resolved sub-question (zero-shot per researcher)
- Critic skipped for `"focused"` questions (latency matters interactively)
- Transcript auto-saved to `.md` on `/save` or Ctrl+C

```
Turn 1:  "How does the payment flow work?"
         → intent: multi-aspect → domain, flows
         → answer: "...StripeGateway.charge() is called after PaymentService..."

Turn 2:  "What if that external call fails?"
         → intent classifier sees last 3 turns
         → resolves: "What happens when StripeGateway.charge() throws an exception?"
         → researcher gets clean sub-question, no history
```

---

## Module Structure

New submodule `codedoc/chat/` — all chat/ask concerns grouped here. Shared infrastructure (`llm.py`, `kg_tools/`, `log.py`, `config.py`) stays in `codedoc/` and is imported directly.

```
pipeline/codedoc/
  chat/
    __init__.py
    agents.py        ← shared pipeline: researcher, synthesizer, critic, revision
    ask.py           ← lumen ask: single-shot runner, writes .md
    session.py       ← lumen chat: multi-turn loop + intent classifier
    prompts/
      researcher.md  ← used by both ask and chat
      synthesizer.md ← used by both ask and chat
      critic.md      ← used by both ask and chat
      intent.md      ← used by chat/session.py only
```

---

## New Files

### `pipeline/codedoc/chat/agents.py`
Shared pipeline used by both `ask` and `chat`:
- `run_pipeline(question, kuzu_path, config, transcript_tail=None)` — full fan-out/fan-in + critic; returns final answer string
- `run_chat_researcher(sub_question, role, kuzu_path, config)` — zero-shot, 3–5 turns, KuzuDB tools only, no history
- `run_chat_synthesizer_draft(question, researcher_notes, transcript_tail, config)` — 1–2 turns
- `run_chat_critic(question, researcher_notes, draft, config)` — 1 turn; returns issues list or empty string
- `run_chat_synthesizer_revision(draft, issues, config)` — 1 turn; skipped if no issues

### `pipeline/codedoc/chat/ask.py`
- `run_ask(question, kuzu_path, output_path, config)` — calls `run_pipeline()`, writes `.md`
- Always fans out to all roles; always runs critic
- Output: markdown with question as heading, evidence tags preserved

### `pipeline/codedoc/chat/session.py`
- `ChatSession(kuzu_path, config)` — holds `transcript`, `kuzu_path`
- `turn(question: str) -> str` — intent classifier → `run_pipeline(transcript_tail=last_3)`
- `run()` — interactive Rich prompt loop; Ctrl+C saves transcript and exits
- Prunes `transcript` when total tokens exceed `max_context_tokens`

### `pipeline/codedoc/chat/prompts/researcher.md`
- Conversational variant of `researcher.md` — bullet points, not full report format
- 3–5 turns max; stop early if confident
- Every claim tagged: [Observed] / [Inferred] / [Hypothesized]
- No history injected — sub-question is always self-contained

### `pipeline/codedoc/chat/prompts/synthesizer.md`
- Conversational tone (not formal doc writer)
- Receives `transcript_tail` (last 3 turns) for continuity
- Draft pass: produce coherent answer, don't overthink
- Revision pass: receives critic issues list; fix only flagged items

### `pipeline/codedoc/chat/prompts/critic.md`
- 1 turn only — not an agent loop
- Checks: contradictions between notes, inferences without graph evidence, gaps, overclaims
- Output: numbered issues list, or "No issues found"

### `pipeline/codedoc/chat/prompts/intent.md`
- Input: user question + last 3 transcript turns
- Resolves: "that class", "it", "the cache layer" → concrete names from prior context
- Output: JSON `{ "type": "focused"|"multi-aspect", "sub_questions": [...], "roles": [...] }`
- `"focused"` → 1 researcher, critic skipped; `"multi-aspect"` → 2–3 researchers, critic runs

---

## Modified Files

### `pipeline/codedoc/cli.py`
Add two new Click commands:

```python
@cli.command()
@click.argument("question")
@click.option("--db", required=True, help="Path to KuzuDB directory")
@click.option("--output", default=None, help="Output .md path (default: artifacts/qa/<slug>.md)")
# + standard --model / --provider / --base-url options
def ask(question, db, output, ...):
    from codedoc.chat.ask import run_ask
    run_ask(question, kuzu_path=db, output_path=output, config=build_config(...))

@cli.command()
@click.option("--db", required=True, help="Path to KuzuDB directory")
@click.option("--save", default=None, help="Save transcript to .md on exit")
# + standard --model / --provider / --base-url options
def chat(db, save, ...):
    from codedoc.chat.session import ChatSession
    session = ChatSession(kuzu_path=db, config=build_config(...))
    session.run()
    if save:
        session.save_transcript(save)
```

`pipeline/pyproject.toml` — no changes needed (entry point `lumen` already wired).

---

## Reused Existing Infrastructure

| Existing | Location | How reused |
|---|---|---|
| `ReverseEngineerToolkit` | `kg_tools/toolkit.py` | All 30+ tools, same interface |
| `run_loop()` | `stages/agent.py` | Agentic loop for each researcher |
| `KuzuBackend` | `kg_tools/backends.py` | One instance per researcher thread |
| `LLMProvider.chat()` | `llm.py` | Single-call for critic / synthesizer / intent classifier |
| `_trim_context()` logic | `stages/agent.py` | Adapt for chat transcript pruning |
| `log.py` helpers | `log.py` | `print_progress_line()`, `print_researcher_done()` |
| Config fields | `config.py` / `state.py` | `model`, `provider`, `base_url`, `max_turns` |

---

## CLI UX

```
$ lumen chat --db output/my-repo-20260403/index.kuzu/my-repo-db

  Lumen Chat — my-repo  (claude-sonnet-4-6)
  Type your question. /save to export transcript. Ctrl+C to exit.

> How does the payment flow work?

  [intent]   multi-aspect → domain, flows              (1 call)
  [domain]   searching entities...       done (2 turns)
  [flows]    tracing entry points...     done (3 turns)
  [draft]    synthesizing...             done
  [critic]   reviewing...                1 issue found
  [revision] fixing...                   done

  Payments are initiated at PaymentController.initiate() [Observed],
  which delegates to PaymentService.process() → StripeGateway.charge().
  StripeGateway is the only external integration in this flow [Observed].

  Follow-up ideas:
  • "What happens if StripeGateway.charge() fails?"
  • "Which classes would break if I change PaymentService?"

> What does UserService do?

  [intent]   focused → domain                          (1 call)
  [domain]   searching entities...       done (2 turns)
  [draft]    synthesizing...             done
  ── critic skipped (focused question) ──

  UserService manages user lifecycle: registration, authentication,
  and profile updates [Observed]. Highest fan-in component — called
  by 12 controllers [Observed].
```

---

## ask Output Format

`artifacts/qa/explain-the-payments-bounded-context-in-depth.md`:

```markdown
# Explain the payments bounded context in depth

_Generated by lumen ask · my-repo · 2026-04-03T14:22 · claude-sonnet-4-6_

---

## Domain Entities

- `Payment` — core aggregate, owns lifecycle from PENDING → PAID → REFUNDED [Observed]
- `PaymentMethod` — value object, stores tokenised card reference [Observed]

## Key Flows

Payments are initiated at `PaymentController.initiate()` [Observed] which delegates
to `PaymentService.process()` → `StripeGateway.charge()` [Observed].

...
```

---

## Follow-On Ideas (out of scope for this phase)

| Idea | Effort | Value |
|---|---|---|
| `lumen impact --class Foo` | Low | High — reuses `impact_analysis()` tool, no fan-out needed |
| `lumen onboard --topic payments` | Medium | Curated learning path for a domain area |

---

## Verification

```bash
# 1. Run pipeline on a test repo
lumen run /path/to/test-repo --provider anthropic --model claude-sonnet-4-6

# --- lumen ask ---
lumen ask "Explain the bounded contexts in depth" \
  --db output/test-repo-*/index.kuzu/test-repo-db
# Verify: artifacts/qa/explain-the-bounded-contexts-in-depth.md written with evidence tags

lumen ask "What are the top coupling hotspots?" \
  --db output/test-repo-*/index.kuzu/test-repo-db \
  --output /tmp/hotspots.md
# Verify: /tmp/hotspots.md written

# --- lumen chat ---
lumen chat --db output/test-repo-*/index.kuzu/test-repo-db

> "What does UserService do?"          # focused → 1 researcher, critic skipped
> "What if I remove it?"               # intent resolves "it" → "UserService"
> "Which tests would break?"           # uses prior context

# Ctrl+C → verify optional transcript save
```

---

## Critical Files to Read Before Implementing

- `pipeline/codedoc/stages/agent.py` — `run_loop()`, `_dispatch_tool()`, parallel researcher pattern
- `pipeline/codedoc/prompts/researcher.md` — base researcher contract to adapt
- `pipeline/codedoc/prompts/synthesizer.md` — base synthesizer to adapt
- `pipeline/codedoc/cli.py` — existing Click command patterns
- `pipeline/codedoc/llm.py` — `LLMProvider`, `Response`, `ToolCall` shapes
- `pipeline/codedoc/kg_tools/toolkit.py` — 30+ available graph tools
