# JS/TS Backend Entry-Point & Middleware Detection

## Background

Investigating whether `security-audit` works across all indexer-supported languages
surfaced that JavaScript/TypeScript has no backend HTTP entry-point strategy at all. Running
the real audit against a MERN app (`Elearning-Platform-Using-MERN`, real Express backend with
JWT `auth` middleware wired via `router.post('/add', auth, handler)`) produced a completely
wrong result: `get_entry_points`/`get_api_endpoints` returned nothing for the actual backend
routes, and the reviewer LLM improvised by treating exported Redux actions (frontend state
management, not a security boundary) as "entry points" — an entirely fabricated analysis that
missed the real routes and the real auth middleware.

Root cause (confirmed by reading code, not guessed):

- `indexer/parsers/javascript/parse.js`'s `CallExpression` handling only inspects
  `node.callee` to build `CALLS` edges — it never inspects `node.arguments`, so
  `router.get('/x', authMiddleware, handler)` produces no edge to `authMiddleware` or
  `handler` at all. The middleware chain is structurally invisible.
- `indexer/app/.../WorkflowBuilder.java`'s JS/React strategy is root-`Component`-based
  (frontend), with `httpMethod`/`httpPath` hardcoded to `null` — there is no
  Express/Koa/Fastify/NestJS backend-route entry-point strategy in the indexer at all.
- `parse.js` tolerates TS/NestJS decorator syntax (`decorators-legacy` Babel plugin) so it
  doesn't choke on `@UseGuards()`/`@Controller()`, but no AST visitor reads `node.decorators`
  — nothing is emitted into the graph for NestJS-style decorator routing either.

This is a bigger gap than the PHP/Python annotation-visibility bugs fixed in the same
session (those were toolkit/schema bugs with one-line fixes) — this needs new indexer parser
logic, similar in scope to the WorkflowBuilder PHP strategy that was added for Laravel.

## Goal

Give JS/TS backend repos (Express, Koa, Fastify, NestJS) real entry-point detection: route
registration (path + HTTP verb + handler) and middleware-chain visibility, so
`security-audit`'s access-control reviewer has real data instead of fabricating findings from
unrelated frontend code.

## Design questions

- **Route registration detection**: `app.get/post/put/delete/patch(path, ...handlers)` and
  Express `Router()` equivalents are the common case — does this belong in `parse.js` (new
  `CallExpression` handling that recognizes these call patterns) or a new post-processing
  strategy in `WorkflowBuilder.java` (mirroring the PHP Laravel strategy)?
- **Middleware-as-annotation**: should middleware function arguments before the final handler
  be captured as `HAS_ANNOTATION`-equivalent edges (matching the Java/PHP schema so
  `get_annotations_usage()` picks them up for free), or as a new edge type
  (`GUARDED_BY`/`HAS_MIDDLEWARE`) since "middleware" isn't really an annotation semantically?
- **NestJS decorators**: `@Controller()`/`@Get()`/`@UseGuards()` are real TS decorators (AST
  `node.decorators`) — this is a different code path from plain Express call-argument
  inspection and would need its own visitor logic.
- **Frontend/backend split in a single repo**: MERN-style repos have both `frontend/` and
  `backend/` JS trees indexed together. Entry-point detection needs to distinguish
  server-side route files from client-side code (likely via directory conventions or import
  of `express`/`koa`/etc.) so the reviewer doesn't see both frontend Redux actions and backend
  routes conflated as "entry points" the way it does today.

## Rough task breakdown

- [ ] Add Express/Koa/Fastify route-registration detection to `parse.js` (or a new
      `WorkflowBuilder` JS-backend strategy)
- [ ] Capture middleware call-chain arguments (not just the final handler) as graph edges
- [ ] Add NestJS decorator-based routing support (`@Controller`/`@Get`/`@Post`/`@UseGuards`)
- [ ] Decide the middleware-signal schema (reuse `HAS_ANNOTATION` vs. new edge type) and wire
      `get_annotations_usage()`/`get_entry_points()`/`get_api_endpoints()` accordingly
- [ ] E2E fixture: a small Express app with an unguarded route and a `middleware`-guarded
      route, to verify both detection and correct auth-signal reporting
- [ ] Update `CLAUDE.md` / `README.md` once shipped (standing rule — see memory)

## Files likely touched

- `indexer/parsers/javascript/parse.js`
- `indexer/app/src/main/java/code/graph/parser/WorkflowBuilder.java` (if a JS-backend
  strategy is added there, mirroring the PHP strategy)
- `pipeline/codedoc/kg_tools/toolkit.py` (`get_entry_points`, `get_api_endpoints`,
  `get_annotations_usage` if a new edge type is introduced)
- `CLAUDE.md`, `README.md`
