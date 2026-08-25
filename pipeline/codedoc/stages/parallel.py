"""Generic fan-out/fan-in concurrency helper for agent pipeline stages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable


def run_parallel_tasks(tasks: dict[str, Callable[[], dict]]) -> dict[str, dict]:
    """Fan out each task on its own thread; fan-in by collecting all results.

    Each task is a no-arg callable returning a result dict (e.g. the dict shape
    returned by ``run_loop``). Callers own how each task builds its own backend/
    toolkit/prompt — this only owns the concurrency and result collection.
    """
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results
