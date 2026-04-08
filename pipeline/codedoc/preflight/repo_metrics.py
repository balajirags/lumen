from __future__ import annotations

from dataclasses import dataclass

from codedoc.repo_classification import classify_repo


@dataclass
class PreflightResult:
    status: str
    summary: str
    warnings: list[str]
    metadata: dict[str, object]
    should_block: bool = False

def collect_repo_metrics(repo_path: str) -> dict[str, object]:
    return classify_repo(repo_path)


def classify_repo_metrics(
    metrics: dict[str, object],
    *,
    max_turns: int,
    max_context_tokens: int,
) -> dict[str, object]:
    total_loc = int(metrics.get("total_loc", 0))
    total_source_files = int(metrics.get("total_source_files", 0))
    detected_languages = list(metrics.get("detected_language_categories", metrics.get("detected_languages", [])))

    if total_loc <= 10_000:
        size_band = "small"
        risk_score = 0
    elif total_loc <= 50_000:
        size_band = "medium"
        risk_score = 1
    elif total_loc <= 150_000:
        size_band = "large"
        risk_score = 2
    else:
        size_band = "xlarge"
        risk_score = 3

    if total_source_files > 1_000:
        risk_score += 1
    if len(detected_languages) >= 3:
        risk_score += 1
    if max_turns <= 60 and total_loc > 50_000:
        risk_score += 1
    if max_context_tokens <= 120_000 and total_loc > 150_000:
        risk_score += 1

    if risk_score <= 0:
        risk_level = "low"
    elif risk_score == 1:
        risk_level = "medium"
    elif risk_score == 2:
        risk_level = "high"
    else:
        risk_level = "critical"

    warning_message = ""
    if risk_level in {"high", "critical"}:
        warning_message = (
            "This repo is large relative to current analysis settings; "
            "results may be partial or slower than expected."
        )

    return {
        **metrics,
        "size_band": size_band,
        "risk_level": risk_level,
        "warning_message": warning_message,
        "guardrail_triggered": bool(warning_message),
    }


class RepoMetricsPreflight:
    name = "repo_metrics"

    def enabled(self, state) -> bool:
        return state.repo_size_check != "off"

    def run(self, repo_path: str, config: dict[str, object]) -> PreflightResult:
        metrics = collect_repo_metrics(repo_path)
        classified = classify_repo_metrics(
            metrics,
            max_turns=int(config.get("max_turns", 60)),
            max_context_tokens=int(config.get("max_context_tokens", 120_000)),
        )
        warning = str(classified.get("warning_message", ""))
        summary = f"{classified['total_loc']:,} LOC across {classified['total_source_files']:,} source files"
        return PreflightResult(
            status="done",
            summary=summary,
            warnings=[warning] if warning else [],
            metadata=classified,
            should_block=False,
        )
