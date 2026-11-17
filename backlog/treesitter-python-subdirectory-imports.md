# treesitter Python extractor loses CALLS edges for code in a subdirectory

Found while building `mini-fullstack-service` for the unified-switch work. The same Python
files indexed standalone produce 2 Workflows; placed under `analytics/` in a larger repo they
produce 0. Python CALLS count dropped 5 -> 2 and qualified names became `analytics.orders...`.

Likely cause: `python/calls.py` resolves `import repository` / `from orders import ...`
relative to the repo root, so code that imports its siblings as top-level modules (a common
`src/` or service-subdirectory layout) no longer resolves. Not a merge issue — reproducible with
`parse_python.py` alone. The fullstack fixture works around it by keeping the Python files at
the repo root.

To do: confirm the cause, then resolve imports relative to the importing file's package root
(or try both) with a fixture that has Python under a subdirectory.
