from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageCategoryDefinition:
    key: str
    display_name: str
    binary_name: str
    compat_alias: str
    cli_language: str | None
    extensions: frozenset[str]
    flavors: frozenset[str]


LANGUAGE_CATEGORIES: tuple[LanguageCategoryDefinition, ...] = (
    LanguageCategoryDefinition(
        key="jvm",
        display_name="JVM (Java/Kotlin)",
        binary_name="cmg-java",
        compat_alias="java",
        cli_language="jvm",
        extensions=frozenset({".java", ".kt", ".kts"}),
        flavors=frozenset({"java", "kotlin"}),
    ),
    LanguageCategoryDefinition(
        key="js",
        display_name="JavaScript/TypeScript",
        binary_name="cmg-js",
        compat_alias="js",
        cli_language=None,
        extensions=frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}),
        flavors=frozenset({"js"}),
    ),
    LanguageCategoryDefinition(
        key="python",
        display_name="Python",
        binary_name="cmg-python",
        compat_alias="python",
        cli_language=None,
        extensions=frozenset({".py", ".pyi"}),
        flavors=frozenset({"python"}),
    ),
    LanguageCategoryDefinition(
        key="php",
        display_name="PHP",
        binary_name="cmg-php",
        compat_alias="php",
        cli_language=None,
        extensions=frozenset({".php", ".phtml", ".php5", ".php7", ".php8"}),
        flavors=frozenset({"php"}),
    ),
)


LANGUAGE_CATEGORY_BY_KEY = {definition.key: definition for definition in LANGUAGE_CATEGORIES}


def category_for_suffix(suffix: str) -> LanguageCategoryDefinition | None:
    suffix = suffix.lower()
    for definition in LANGUAGE_CATEGORIES:
        if suffix in definition.extensions:
            return definition
    return None

