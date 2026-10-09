"""
Generic, call-site-shaped HTTP route resolution -- the sibling to
http_routes.py's annotation-shaped resolve_route(), for routing DSLs that
register routes via a method CALL with a verb-named method and a path
argument (Laravel's Route::get($path, $handler), Express's
router.get(path, handler), Ktor's get(path) { ... }) rather than an
annotated declaration.

Deliberately reuses http_routes.py's HTTP_VERBS/_tokenize/_ROUTE_HINT_WORDS
machinery rather than duplicating it -- the verb vocabulary is the same
closed, protocol-level set regardless of whether it's read from an
annotation name or a call's method name.

Recognizing WHICH calls are routing calls in the first place (e.g. "this is
a `Route::` static call") is deliberately NOT this function's job -- that
recognition is unavoidably specific to each framework's own entry point
(Laravel's `Route` facade, Express's `app`/`router` object, Ktor's
`routing {}` block), and lives in each language's own extractor. This keeps
the one piece that actually generalizes -- verb/path extraction from a
(calleeName, args) pair -- in one reusable place.
"""

from __future__ import annotations

from postprocess.http_routes import HTTP_VERBS, _ROUTE_HINT_WORDS, _tokenize

# "any"/"match"/"all" are call-based routing's own "registers for any/
# multiple HTTP verbs" vocabulary (Laravel's Route::any()/Route::match(),
# Express's router.all()) -- a different concept from _ROUTE_HINT_WORDS
# (which flags an annotation/call NAME as route-shaped in general, not as
# "matches any verb specifically"), so kept local to this module rather than
# added to the shared annotation-oriented vocabulary in http_routes.py.
_ANY_VERB_CALLEE_WORDS = {"any", "all", "match"}


def resolve_call_route(callee_name: str, args: list) -> tuple[str, str | None] | None:
    """Given a call's method name (e.g. "get", "post", "middleware") and its
    positional argument list (already reduced to plain Python values --
    strings for string literals, None for anything else, same convention as
    http_routes.py's annotation value), returns (httpMethod, httpPath) if
    this call-site looks like a route registration in ANY call-based
    framework, else None.

    Only the FIRST positional argument is ever treated as the path (mirrors
    Laravel's Route::get($path, $handler) argument order; also correct for
    Express's router.get(path, handler) and Ktor's get(path) { ... }) -- if
    that argument isn't a non-empty string (e.g. it's the handler itself,
    because no path was actually passed), the path is None rather than
    hunting further down the argument list for some other string.
    """
    tokens = _tokenize(callee_name)
    verb = next((t for t in tokens if t in HTTP_VERBS), None)
    is_any_verb = any(t in _ANY_VERB_CALLEE_WORDS for t in tokens)
    is_route_hint = any(t in _ROUTE_HINT_WORDS for t in tokens)
    if verb is None and not is_any_verb and not is_route_hint:
        return None

    path = args[0] if args and isinstance(args[0], str) and args[0] else None
    return ((verb or "http").upper(), path)
