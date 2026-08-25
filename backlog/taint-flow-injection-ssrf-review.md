# Injection & SSRF Taint-Flow Reviewer for `security-audit`

## Background

Mapping `security-audit`'s current coverage against OWASP Top 10 (2021) surfaced that A03
(Injection) and A10 (SSRF) have zero dedicated coverage — no reviewer traces "attacker-
controlled input reaches a dangerous sink." Ironically, the manually-reviewed audit report
for `mini-flask-app` *did* catch a real SSRF (`export_user`'s unvalidated `user_id`
interpolated into `https://billing.internal/users/{user_id}`), but only because the
synthesis step happened to read the reviewers' prose closely — there is no graph-based check
that would catch this systematically on a larger repo where nobody reads every line.

This is the natural next reviewer to add after `backlog/dependency-cve-scanning.md` because the
toolkit already has the primitive it needs (`get_data_flow`, `trace_user_flow`) — unlike CVE
scanning, this does **not** need new indexer extraction or an external network call. It's the
same shape as the threat-model reviewer addition: a new prompt + a possible new toolkit
method, no architecture change.

## Goal

Add a reviewer that traces entry-point input to known dangerous sinks (SQL/shell/eval
execution, outbound HTTP calls built from interpolated strings, file path construction) and
flags unvalidated-input paths as Injection or SSRF findings.

## Design questions

- **Sink detection**: is there already enough graph signal (call target names like
  `os.system`, `subprocess.*`, `eval`, `exec`, `cursor.execute`, `requests.get`/`requests.post`
  with an f-string/format argument) to detect this via a `query()` Cypher pattern match on
  known sink name substrings, or does this need a new toolkit method
  (`get_dangerous_sink_calls()`) that curates a per-language sink list so the reviewer isn't
  writing raw Cypher from scratch each run?
- **Taint direction**: `get_data_flow(method_name)` gives variable propagation within one
  method — is that sufficient to say "parameter X flows into sink call Y", or does this need
  multi-hop tracing across `trace_user_flow`'s call chain (parameter passed to a helper, which
  passes it to the sink)? The `mini-flask-app` example needed exactly this: `user_id` flows
  from `export_user` into `repo.find_full_record(user_id)`, which flows into the outbound
  `requests.get` call — a 2-hop taint chain the current tools weren't asked to establish.
- **False positive rate**: sink-name matching without real taint analysis will over-flag
  (e.g. a hardcoded, non-attacker-controlled string reaching `subprocess.run`). Evidence tags
  (`[Observed]` = sink call confirmed, `[Inferred]` = taint path assumed) need to make this
  uncertainty explicit rather than reading as a confirmed vulnerability.
- **Scope**: one combined reviewer for Injection + SSRF (they share the same taint-tracing
  mechanism), or split into two artifacts? Leaning toward combined, given both need identical
  entry-point → sink tracing and the split would duplicate most of the prompt.

## Rough task breakdown

- [ ] Curate a per-language dangerous-sink pattern list (SQL exec, shell exec, eval/exec,
      outbound HTTP with dynamic URL, deserialization calls) — likely a new toolkit method
      rather than hand-written Cypher per prompt
- [ ] Verify `get_data_flow` + `trace_user_flow` are sufficient for multi-hop taint tracing, or
      identify what's missing (e.g. a `trace_taint(entry_point, sink_pattern)` helper)
- [ ] New prompt `security-injection-ssrf.md` → `security/injection-ssrf-findings.md`, same
      `[Observed]`/`[Inferred]` discipline as the other reviewers, explicit turn-budget
      contract (learned from the threat-model reviewer's first draft running out of turns)
- [ ] Add as a 4th parallel reviewer in `security_audit_agent.py` (extend
      `_REVIEWER_PROMPT_FILES`/`_REVIEWER_ARTIFACTS`/`_ALL_REVIEWER_ARTIFACTS`, same pattern as
      the threat-model addition)
- [ ] Update `security-synthesis.md` reviewer count/injection loop (4 reviewers now)
- [ ] E2E fixture with a known injection or SSRF pattern (the existing `mini-flask-app`
      fixture's `export_user`/`billing.internal` case already qualifies) to verify detection
- [ ] Update `CLAUDE.md` / `README.md` once shipped (standing rule — see memory)

## Files likely touched

- `pipeline/codedoc/kg_tools/toolkit.py` — possible new `get_dangerous_sink_calls()` /
  `trace_taint()` method
- `pipeline/codedoc/prompts/security-injection-ssrf.md` (new)
- `pipeline/codedoc/prompts/security-synthesis.md`
- `pipeline/codedoc/stages/security_audit_agent.py`
- `CLAUDE.md`, `README.md`
