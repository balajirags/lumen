#!/usr/bin/env bash
#
# test-lumen.sh — lightweight regression safety net for lumen pipeline changes.
#
# Fast path (default, zero setup): discovers every native pipeline command from the
# Makefile (e.g. `lumen-run`, `lumen-security-audit` — anything matching `lumen-<name>:`
# except docker/mcp/docs/install/build/help/test targets) and runs each one via
# `make lumen-<name> REPO=...` against small synthetic fixture repos checked into
# tests/fixtures/. A new pipeline that follows the Makefile naming convention
# (see docs/adding-a-pipeline.md) is picked up automatically — no edits needed here.
#
# Each run is verified structurally, not just by exit code:
#   - pipeline.json exists, status == "done", mode matches the pipeline
#   - artifacts_dir exists
#   - if the pipeline recorded a formal artifact_plan (the docs/full pipeline does), every
#     required artifact from that plan is present; otherwise a minimum-file-count check
#   - every non-empty artifact file found
#
# Opt-in real-repo pass: add absolute paths to REAL_REPOS below (or set REAL_REPOS_ENV,
# space-separated) to additionally run every discovered pipeline against real checked-out
# repos. Slower and costs real LLM tokens — off by default.
#
# Env overrides: PROVIDER, MODEL, BASE_URL, MAX_TURNS, REAL_REPOS_ENV.

set -uo pipefail  # not -e: we want to run every check and report all failures, not bail early

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

PROVIDER="${PROVIDER:-anthropic}"
MODEL="${MODEL:-claude-sonnet-4-6}"
BASE_URL="${BASE_URL:-}"
MAX_TURNS="${MAX_TURNS:-30}"

LOG_DIR="$REPO_ROOT/task_logs"
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# Fixture repos (fast path) — tiny, checked-in, free/cheap to index and analyze.
# ---------------------------------------------------------------------------
FIXTURES=(
    "$REPO_ROOT/tests/fixtures/mini-flask-app"
)

# ---------------------------------------------------------------------------
# Opt-in real-repo pass. Empty by default — uncomment/add paths for a slower,
# realistic-scale run, or set REAL_REPOS_ENV="/path/one /path/two".
# ---------------------------------------------------------------------------
REAL_REPOS=(
    # "/Users/gbalaji/projects/tw/inventory-service"
    # "/Users/gbalaji/projects/tw/ocm_pd_component_price"
    # "/Users/gbalaji/projects/personal/open-source/Elearning-Platform-Using-MERN"
    # "/Users/gbalaji/projects/personal/open-source/job-winner"
    # "/Users/gbalaji/projects/personal/open-source/medusa"
    # "/Users/gbalaji/projects/personal/open-source/ocm_pd_component_price"
    # "/Users/gbalaji/projects/personal/open-source/openmrs-core"
    # "/Users/gbalaji/projects/personal/open-source/mercur"
)
if [ -n "${REAL_REPOS_ENV:-}" ]; then
    # shellcheck disable=SC2206
    REAL_REPOS+=( ${REAL_REPOS_ENV} )
fi

ARGS_COMMON="--provider ${PROVIDER} --model ${MODEL} --max-turns ${MAX_TURNS}"
if [ -n "$BASE_URL" ]; then
    ARGS_COMMON="${ARGS_COMMON} --base-url ${BASE_URL}"
fi

PASS=0
FAIL=0
FAILED_RUNS=()

# ---------------------------------------------------------------------------
# Discover native pipeline commands from the Makefile. Excludes docker variants
# (image-build cost, not what a fast local safety net needs), mcp (index + serve
# only, no agent stage to regress the same way), and non-pipeline targets.
# ---------------------------------------------------------------------------
discover_pipeline_targets() {
    grep -E '^lumen-[a-z][a-z0-9-]*:' "$REPO_ROOT/Makefile" \
        | sed -E 's/^(lumen-[a-z0-9-]+):.*/\1/' \
        | grep -v '^lumen-docker-' \
        | grep -vE '^(lumen-help|lumen-install|lumen-install-indexer|lumen-install-pipeline|lumen-native-build|lumen-test|lumen-mcp)$' \
        | sort -u
}

# `init_state(mode=...)` uses the CLI command's kebab-case name for every pipeline added
# after security-audit (see docs/adding-a-pipeline.md's naming convention) — except the
# original docs pipeline, which predates that convention and uses mode="full" for the
# `run` command. This is the one legacy exception; every future pipeline should NOT need
# an entry here.
expected_mode_for() {
    case "$1" in
        run) echo "full" ;;
        *) echo "$1" ;;
    esac
}

# `run_indexer` (shared, unmodified across all pipelines) always populates
# `state.artifact_plan` as part of repo classification — but only the docs/`full`
# pipeline's agent stage actually promises to fulfill that plan (see
# archetype_registry.py / artifact_planner.py). docs/adding-a-pipeline.md explicitly
# tells new pipelines NOT to use this system, so `artifact_plan` being present in
# pipeline.json does not mean a given pipeline is plan-driven — only `run` is today.
uses_artifact_plan_for() {
    case "$1" in
        run) echo "1" ;;
        *) echo "0" ;;
    esac
}

find_latest_run_dir() {
    local repo_name="$1" prefix
    prefix="${repo_name:0:20}"
    ls -1dt "$REPO_ROOT"/output/"${prefix}"-* 2>/dev/null | head -1
}

verify_run() {
    local expected_mode="$1" run_dir="$2" use_plan_check="$3"
    local pj="$run_dir/pipeline.json"
    if [ ! -f "$pj" ]; then
        echo "  missing pipeline.json in $run_dir" >&2
        return 1
    fi
    python3 - "$pj" "$expected_mode" "$run_dir" "$use_plan_check" <<'PYEOF'
import glob
import json
import os
import sys

pj_path, expected_mode, run_dir, use_plan_check = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
data = json.load(open(pj_path))

status = data.get("status")
if status != "done":
    print(f"  status={status!r}, expected 'done'", file=sys.stderr)
    sys.exit(1)

mode = data.get("mode")
if mode != expected_mode:
    print(f"  mode={mode!r}, expected {expected_mode!r}", file=sys.stderr)
    sys.exit(1)

artifacts_dir = data.get("artifacts_dir") or os.path.join(run_dir, "artifacts")
if not os.path.isdir(artifacts_dir):
    print(f"  artifacts_dir missing: {artifacts_dir}", file=sys.stderr)
    sys.exit(1)

found = sorted(
    p for p in glob.glob(os.path.join(artifacts_dir, "**", "*"), recursive=True)
    if os.path.isfile(p) and os.path.getsize(p) > 0
)

plan = data.get("artifact_plan") or {}
required = [
    item["path"] for item in plan.get("artifacts", [])
    if item.get("required") and item.get("class") != "manifest"
] if use_plan_check else []
if required:
    missing = [p for p in required if not os.path.isfile(os.path.join(artifacts_dir, p))]
    if missing:
        print(f"  missing required artifacts from plan: {missing}", file=sys.stderr)
        sys.exit(1)
    print(f"  ok — {len(required)} required artifact(s) from plan present, {len(found)} file(s) total")
else:
    MIN_FILES = 2
    if len(found) < MIN_FILES:
        print(f"  only {len(found)} non-empty artifact file(s) under {artifacts_dir}, expected >= {MIN_FILES}", file=sys.stderr)
        sys.exit(1)
    print(f"  ok — {len(found)} artifact file(s) (not a plan-driven pipeline; used minimum-count check)")
PYEOF
}

run_one() {
    local make_target="$1" repo="$2"
    local cli_name="${make_target#lumen-}"
    local expected_mode
    expected_mode="$(expected_mode_for "$cli_name")"
    local repo_name
    repo_name="$(basename "$repo")"
    local log_file="$LOG_DIR/${make_target}__${repo_name}.log"

    echo "[$(date +"%T")] RUN   ${make_target} / ${repo_name}"
    local start end duration
    start=$(date +%s)

    if make "${make_target}" REPO="${repo}" ARGS="${ARGS_COMMON}" > "${log_file}" 2>&1; then
        exit_ok=1
    else
        exit_ok=0
    fi
    end=$(date +%s); duration=$((end - start))

    if [ "$exit_ok" -ne 1 ]; then
        echo "[$(date +"%T")] FAIL  ${make_target} / ${repo_name}  (exit code, ${duration}s) — see ${log_file}"
        FAIL=$((FAIL + 1))
        FAILED_RUNS+=("${make_target} / ${repo_name}  — exit code, see ${log_file}")
        return
    fi

    local run_dir
    run_dir="$(find_latest_run_dir "$repo_name")"
    if [ -z "$run_dir" ]; then
        echo "[$(date +"%T")] FAIL  ${make_target} / ${repo_name}  (no run dir found under ./output, ${duration}s)"
        FAIL=$((FAIL + 1))
        FAILED_RUNS+=("${make_target} / ${repo_name}  — no run dir found, see ${log_file}")
        return
    fi

    local use_plan_check
    use_plan_check="$(uses_artifact_plan_for "$cli_name")"
    if verify_output="$(verify_run "$expected_mode" "$run_dir" "$use_plan_check" 2>&1)"; then
        echo "[$(date +"%T")] PASS  ${make_target} / ${repo_name}  (${duration}s)  → ${run_dir}"
        echo "$verify_output" | sed 's/^/         /'
        PASS=$((PASS + 1))
        # Deliberately NOT auto-deleting run_dir: `lumen run`'s builder stage links
        # ./output/doc-site/artifacts to a specific run's artifacts dir (the doc-site is
        # a shared, cross-run accumulation directory by design — see CLAUDE.md). Deleting
        # a run dir out from under that shared symlink causes the *next* run to fail with
        # "FileExistsError: doc-site/artifacts" (a real, separate pre-existing bug in
        # stages/builder.py's fallback path — see its `artifacts_link.exists()` check,
        # which doesn't detect a dangling symlink). Run `rm -rf ./output` periodically by
        # hand instead of relying on this script to clean up after itself.
    else
        echo "[$(date +"%T")] FAIL  ${make_target} / ${repo_name}  (structural check, ${duration}s) — see ${log_file}"
        echo "$verify_output" | sed 's/^/         /'
        echo "         run dir kept for inspection: ${run_dir}"
        FAIL=$((FAIL + 1))
        FAILED_RUNS+=("${make_target} / ${repo_name}  — structural check, see ${log_file} and ${run_dir}")
    fi
}

TARGETS=($(discover_pipeline_targets))
if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "No pipeline targets discovered from Makefile — check discover_pipeline_targets()." >&2
    exit 1
fi

echo "Discovered pipeline targets: ${TARGETS[*]}"
echo "Fixture repos: ${#FIXTURES[@]}   Real repos (opt-in): ${#REAL_REPOS[@]}"
echo "--------------------------------------------------------"

for target in "${TARGETS[@]}"; do
    for repo in "${FIXTURES[@]}"; do
        run_one "$target" "$repo"
    done
done

if [ ${#REAL_REPOS[@]} -gt 0 ]; then
    echo "--------------------------------------------------------"
    echo "Real-repo pass (opt-in)…"
    for target in "${TARGETS[@]}"; do
        for repo in "${REAL_REPOS[@]}"; do
            run_one "$target" "$repo"
        done
    done
fi

echo "--------------------------------------------------------"
echo "Done. PASS=${PASS} FAIL=${FAIL}"
if [ "$FAIL" -gt 0 ]; then
    echo "Failures:"
    for f in "${FAILED_RUNS[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
exit 0
