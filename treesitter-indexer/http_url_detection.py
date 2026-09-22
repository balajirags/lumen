"""
Detects HTTP-based dependencies via a single generic, framework-agnostic
signal: a string literal shaped like a URL (http:// or https://), wherever it
appears -- an annotation/decorator attribute value or a call argument.
Deliberately does NOT special-case any specific framework/library (no
@FeignClient, no RestTemplate/WebClient method-name allowlist, no Python
`requests`/`httpx` allowlist): a URL literal is inherently generic evidence
regardless of which annotation/decorator/method it's attached to.

Trade-off, accepted deliberately: a purely name-based service identifier with
no URL at all (e.g. a Feign client's bare `name = "inventory-service"`, which
relies on service discovery) won't be captured -- there's no generic way to
distinguish "this string is a service name" from any other string literal
without hardcoding a specific framework's convention.

Promoted out of java/http_dependencies.py (pure move, no behavior change) so
every language's extractor can share one predicate instead of each language
re-implementing it.
"""

from __future__ import annotations


def looks_like_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")
