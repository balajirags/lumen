"""
Generic, framework-agnostic architectural-layer classification for
DomainDetector, replacing a hardcoded Spring-stereotype-annotation map.

Same tokenization approach as http_routes.py, but a different kind of
vocabulary: HTTP verbs are a closed, protocol-level vocabulary (part of the
HTTP spec, not any framework's choice), whereas "Controller"/"Service"/
"Repository" are just common English words Spring, Micronaut, and plain
package-by-layer Java conventions all happen to reuse for the same
architectural roles -- there's no spec mandating them. So instead of matching
one framework's exact annotation names, this matches the underlying words
wherever they appear: in an annotation name (Spring's @RestController,
Micronaut's @Controller, a hypothetical @ApiController), OR in a class's
package/qualified name when there's no stereotype annotation at all (JAX-RS
apps, plain package-by-layer Java) -- which is also why the previous
`.model.`/`.entity.`/`.dto.` package-substring check is folded into the same
word list here instead of living as a separate special case.
"""

from __future__ import annotations

import re

_LAYER_WORDS = {
    "controller": "Controller",
    "resource": "Controller",  # JAX-RS's own term for this role (e.g. ItemsResource)
    "service": "Service",
    "repository": "Repository",
    "dao": "Repository",  # older/pre-Spring-Data Java convention
    "component": "Component",
    "configuration": "Configuration",
    "config": "Configuration",
    "model": "Model",
    "entity": "Model",
    "dto": "Model",
}


def _tokenize(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [p.lower() for p in re.split(r"[\s_.\-/]+", spaced) if p]


def layer_from_annotation(annotation_name: str) -> str | None:
    """Returns a normalized layer label if this annotation name suggests an
    architectural stereotype, regardless of which framework defines it --
    e.g. Spring's RestController, Micronaut's Controller, JPA's Entity, or a
    hypothetical ApiController/DataRepository all match the same way."""
    for token in _tokenize(annotation_name):
        if token in _LAYER_WORDS:
            return _LAYER_WORDS[token]
    return None


def layer_from_qualified_name(qualified_name: str) -> str | None:
    """Fallback for classes with no stereotype annotation at all (JAX-RS,
    plain package-by-layer Java): checks package segments and the class name
    itself for the same architectural role words."""
    for token in _tokenize(qualified_name):
        if token in _LAYER_WORDS:
            return _LAYER_WORDS[token]
    return None
