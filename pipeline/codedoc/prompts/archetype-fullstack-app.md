Treat the repository as a fullstack application with both frontend and backend concerns.

- Preserve the end-to-end story: UI routes, state, API calls, backend entry points, and persistence/integration boundaries should line up.
- Prefer artifacts that explain seams and ownership across frontend and backend instead of forcing a pure frontend or pure backend framing.
- Use C4 only for repo-level context. Use Mermaid for journey, route, and boundary views.
- Do not invent separate services, BFF layers, or event platforms unless the graph or current-state artifacts provide direct evidence.
