# PHP/Laravel `Route::middleware()` Detection

## Background

While verifying `security-audit`'s language coverage, a controlled Laravel-style fixture
(routes wrapped in `Route::middleware('auth')->group(function () { ... })`) confirmed a real
gap: `indexer/parsers/php/parse.php`'s `RouteExtractor` (lines ~451-537) only matches direct
`Route::get/post/put/delete/patch/any/match('/path', [Controller::class, 'method'])` static
calls. It never handles `->middleware(...)` method calls or `Route::middleware(...)->group()`
closures — the visitor pattern-matches `StaticCall` nodes for `Route`, and `->middleware()`
calls are `MethodCall` nodes the visitor never inspects; group closures aren't traversed
into for auth purposes either.

This was fixed together with a *different*, more severe PHP bug in the same investigation:
the `HAS_ANNOTATION` KuzuDB write path for PHP was silently dropping 100% of route/annotation
edges due to a schema mismatch (missing `value` property on the `HAS_ANNOTATION` REL TABLE in
`indexer/parsers/python/store.py`, which the PHP bridge writer shares). That bug meant PHP
route detection was completely broken (zero entry points detected at all). With that fixed,
routes now register correctly with the right HTTP verb — but a route's presence *inside* a
`Route::middleware('auth')->group()` block still produces no observable auth signal, so a
fully-protected Laravel route looks identical, to `get_annotations_usage()`, to a totally
unprotected one. Confirmed directly: a fixture with one public route and three
`auth`-middleware-protected routes reported "no authorization annotations observed anywhere in
the codebase" for all four routes.

## Goal

Make `Route::middleware(...)` (both the fluent single-route form and the
`Route::middleware(...)->group(fn () => {...})` form) visible in the graph, so protected
Laravel routes are distinguishable from unprotected ones.

## Design questions

- **Schema**: reuse the existing `ANNOTATION_TYPE`/`HAS_ANNOTATION` pattern (emit a synthetic
  `Middleware` annotation type per middleware name, e.g. `HAS_ANNOTATION {value: 'auth'}` on
  every route inside the group) so `get_annotations_usage()` picks it up for free with zero
  toolkit changes — or introduce a dedicated edge type? Reusing `HAS_ANNOTATION` is likely
  simpler and consistent with how HTTP-verb routes are already represented as annotations.
- **Group traversal**: `Route::middleware(...)->group(closure)` requires walking into the
  closure's statements and applying the middleware to every `Route::<verb>(...)` call found
  inside — including nested groups (Laravel allows `Route::middleware('auth')->group(fn () =>
  { Route::middleware('admin')->group(fn () => {...}); })`).
- **Controller-level middleware**: Laravel also supports `$this->middleware('auth')` inside a
  controller's constructor, applying to all (or some, via `->only()`/`->except()`) of that
  controller's methods — a separate detection path from route-file parsing.

## Rough task breakdown

- [ ] Handle `Route::middleware('name')->get/post/...(...)` (fluent single-route form)
- [ ] Handle `Route::middleware('name')->group(closure)` — walk closure body, apply to every
      nested `Route::<verb>(...)` call, recursively for nested groups
- [ ] Handle controller-constructor `$this->middleware('name')` as a fallback signal
- [ ] Emit as `HAS_ANNOTATION` (or a decided alternative) so existing toolkit tools pick it up
      without changes
- [ ] Re-verify against a fixture with public + `auth`-protected + nested-group routes
- [ ] Update `CLAUDE.md` / `README.md` once shipped (standing rule — see memory)

## Files likely touched

- `indexer/parsers/php/parse.php` (`RouteExtractor`)
- `CLAUDE.md`, `README.md`
