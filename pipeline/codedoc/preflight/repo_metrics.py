from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


_LANG_EXTS = {
    "java": {".java", ".kt", ".kts"},
    "js": {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"},
    "python": {".py", ".pyi"},
}

_IGNORED_PARTS = {"node_modules", "__pycache__", "venv", ".venv", "build", "dist", "target"}


@dataclass
class PreflightResult:
    status: str
    summary: str
    warnings: list[str]
    metadata: dict[str, object]
    should_block: bool = False


def _detect_language(path: Path) -> str | None:
    ext = path.suffix.lower()
    for language, extensions in _LANG_EXTS.items():
        if ext in extensions:
            return language
    return None


def _is_ignored(path: Path) -> bool:
    return any(part.startswith(".") or part in _IGNORED_PARTS for part in path.parts)


def collect_repo_metrics(repo_path: str) -> dict[str, object]:
    repo = Path(repo_path)
    files_by_language = {name: 0 for name in _LANG_EXTS}
    loc_by_language = {name: 0 for name in _LANG_EXTS}

    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if _is_ignored(rel.parent):
            continue
        language = _detect_language(path)
        if not language:
            continue
        files_by_language[language] += 1
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                loc_by_language[language] += sum(1 for line in handle if line.strip())
        except OSError:
            continue

    detected_languages = [lang for lang, count in files_by_language.items() if count > 0]
    total_loc = sum(loc_by_language.values())
    total_source_files = sum(files_by_language.values())
    return {
        "total_loc": total_loc,
        "total_source_files": total_source_files,
        "loc_by_language": {k: v for k, v in loc_by_language.items() if v > 0},
        "files_by_language": {k: v for k, v in files_by_language.items() if v > 0},
        "detected_languages": detected_languages,
    }


def classify_repo_metrics(
    metrics: dict[str, object],
    *,
    max_turns: int,
    max_context_tokens: int,
) -> dict[str, object]:
    total_loc = int(metrics.get("total_loc", 0))
    total_source_files = int(metrics.get("total_source_files", 0))
    detected_languages = list(metrics.get("detected_languages", []))

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
