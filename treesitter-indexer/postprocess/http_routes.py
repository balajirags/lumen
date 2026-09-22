"""
Generic, framework-agnostic HTTP route/entry-point detection for
WorkflowBuilder, replacing a hardcoded Spring-only annotation list.

Same philosophy as java/http_dependencies.py: recognize routes by
STRUCTURAL SHAPE (an annotation name that normalizes to an HTTP verb, plus a
path-shaped attribute value) rather than a fixed per-framework name list.
Spring's GetMapping/PostMapping/RequestMapping, Micronaut's Get/Post, and
JAX-RS's GET/POST + Path all match the same generic rules below without any
of those names being hardcoded -- only the small, closed set of HTTP verb
words themselves (get/post/put/...) is fixed, since that vocabulary is part
of the HTTP spec, not any one framework's convention.

Deliberately out of scope here (would need a genuinely different mechanism,
not just a bigger pattern): DSL/call-based routing (Express's app.get(path,
handler), Spark Java's get(path, handler), Ktor's get(path) { ... }) --
those register routes via a method CALL with a lambda/method-reference
handler, not an annotated method, so there's no "entry point method" to
attach a Workflow's BFS start to without a deeper design (tracing into the
handler argument). Flagged as a follow-up, not attempted here.
"""

from __future__ import annotations

import re

HTTP_VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}

# Words that mark an annotation as route-related even when it names no verb
# of its own (Spring's RequestMapping is the common case: the verb, if any,
# lives in a separate `method` attribute this project can't see -- see
# resolve_route's docstring; JAX-RS's @Path is the other common case: the
# verb lives on a co-located @GET/@POST/etc. annotation instead).
_ROUTE_HINT_WORDS = {"mapping", "route", "routing", "endpoint", "path", "http"}


def _tokenize(name: str) -> list[str]:
    """Splits a PascalCase/camelCase/underscore/hyphen name into lowercase
    word tokens, e.g. "PostMapping" -> ["post","mapping"], "HttpDelete" ->
    ["http","delete"], "GET" -> ["get"]. Lets annotation_http_verb recognize
    a verb wherever it appears in the name (prefix, suffix, whole name)
    instead of only exact matches or fixed suffixes."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [p.lower() for p in re.split(r"[\s_-]+", spaced) if p]


def annotation_http_verb(annotation_name: str) -> str | None:
    """Returns a normalized verb ('get', 'post', ...), 'any' (a route
    annotation with no verb of its own, e.g. RequestMapping or a bare @Path),
    or None (not a route annotation at all) for an annotation name --
    framework-agnostic by construction. Tokenizing first (rather than a plain
    substring/prefix check) is what lets "Getter" (Lombok) correctly NOT
    match "get" while "HttpDelete" correctly DOES match "delete"."""
    tokens = _tokenize(annotation_name)
    verb = next((t for t in tokens if t in HTTP_VERBS), None)
    if verb:
        return verb
    if any(t in _ROUTE_HINT_WORDS for t in tokens):
        return "any"
    return None


def looks_like_route_path(text: str) -> bool:
    return text.startswith("/")


def resolve_route(annotations: dict[str, str]) -> tuple[str, str | None] | None:
    """Given a method's {annotationName: capturedValue} map (from
    HAS_ANNOTATION -- capturedValue is already collapsed to one fallback-first
    attribute per annotation, see extractor.py's _annotation_name_and_value),
    returns (httpMethod, httpPath) if this method looks like an HTTP entry
    point in ANY annotation-based framework, else None.

    Combines signals across co-located annotations on the same method, which
    is what makes JAX-RS's two-annotation style (@GET + @Path("/x")) work
    without any JAX-RS-specific code: the verb comes from whichever
    annotation's name matches one, the path from whichever annotation's
    captured value looks like a path -- they don't have to be the same
    annotation. Known, accepted limitation: Spring's single-annotation
    `@RequestMapping(value = "/x", method = RequestMethod.GET)` only ever
    captures one of its two attributes (whichever HAS_ANNOTATION happened to
    keep), so the `method` attribute is invisible here -- same limitation the
    hardcoded predecessor had, not a regression.
    """
    verb: str | None = None
    path: str | None = None
    saw_route_annotation = False
    for ann_name, value in annotations.items():
        matched = annotation_http_verb(ann_name)
        if matched is not None:
            saw_route_annotation = True
            if matched != "any" and verb is None:
                verb = matched.upper()
        if value and looks_like_route_path(value):
            path = path or value
    if not saw_route_annotation:
        return None
    return (verb or "HTTP", path)
