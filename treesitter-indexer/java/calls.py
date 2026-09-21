"""
CALLS edge resolution with confidence tiers, mirroring
indexer/app/.../JavaSourceParser.java's tier scheme (verified by direct read):

  0.95 "same-file"      -- call target's declaring type is the caller's own
                            enclosing type (an implicit/`this`-scoped call).
  0.90 "import-resolved" -- call's receiver is a local var/field/import whose
                            declared type resolves to a known internal type.
  0.50 "global"          -- only the raw, unresolved receiver text is
                            available, and it happens to textually match an
                            internal type's simple name.

All tiers are filtered through the internal-types set built by extractor.py --
calls to types outside the parsed corpus produce no node/edge at all, exactly
as JavaSourceParser does. Unlike JavaSourceParser (which uses JavaParser's
JavaSymbolSolver for real overload resolution), this resolves by (declared
type, method name, argument count) rather than full parameter-type matching --
a deliberate simplification. Generics-heavy overload resolution will
occasionally misclassify tier or miss entirely; this is an accepted,
documented divergence, not a bug to chase.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepoIndex:
    """Repo-wide facts extractor.py builds during Pass 1, consumed here."""

    internal_types: set[str] = field(default_factory=set)
    type_file: dict[str, str] = field(default_factory=dict)
    type_package: dict[str, str] = field(default_factory=dict)
    simple_name_to_fqns: dict[str, list[str]] = field(default_factory=dict)
    file_imports: dict[str, dict[str, str]] = field(default_factory=dict)
    file_wildcard_packages: dict[str, list[str]] = field(default_factory=dict)

    def resolve_type_name(self, simple_or_qualified: str, from_file: str, from_package: str) -> str | None:
        """Best-effort resolution of a source-level type name to an internal FQN."""
        name = simple_or_qualified.strip()
        if not name:
            return None
        # Strip generics / array brackets, e.g. List<String> -> List, String[] -> String
        if "<" in name:
            name = name[: name.index("<")]
        name = name.rstrip("[]").strip()
        if not name:
            return None

        if name in self.internal_types:
            return name

        # Same-package convention: package.SimpleName
        candidate = f"{from_package}.{name}" if from_package else name
        if candidate in self.internal_types:
            return candidate

        # Explicit import in this file
        imports = self.file_imports.get(from_file, {})
        if name in imports and imports[name] in self.internal_types:
            return imports[name]

        # Wildcard import: try each wildcard package prefix
        for pkg in self.file_wildcard_packages.get(from_file, []):
            candidate = f"{pkg}.{name}"
            if candidate in self.internal_types:
                return candidate

        # Last resort: unique simple-name match anywhere in the repo
        matches = self.simple_name_to_fqns.get(name, [])
        if len(matches) == 1:
            return matches[0]
        return None

    def qualify_external(self, simple_or_qualified: str, from_file: str) -> str:
        """Best-effort qualified name for a type NOT in this repo (framework/JDK
        classes), used so external placeholder nodes look like real qualified
        names (e.g. `org.springframework...MessageListener`) rather than a bare
        simple name -- mirrors what real symbol resolution would produce, using
        only this file's own import statements (no classpath JARs available)."""
        name = simple_or_qualified.strip()
        if "<" in name:
            name = name[: name.index("<")]
        name = name.rstrip("[]").strip()
        if "." in name:
            return name
        imports = self.file_imports.get(from_file, {})
        return imports.get(name, name)


@dataclass
class MethodCallContext:
    """Per-call-site facts extractor.py gathers while walking one method body."""

    caller_type_fqn: str
    caller_file: str
    caller_package: str
    local_types: dict[str, str]  # varName -> declared type text (unresolved)
    field_types: dict[str, str]  # fieldName -> declared type text (unresolved), same type only


def resolve_call_target(
    receiver_text: str | None,
    method_name: str,
    arg_count: int,
    ctx: MethodCallContext,
    index: RepoIndex,
) -> tuple[str, float, str] | None:
    """Returns (target_type_fqn, confidence, reason) or None if unresolvable/external."""

    # No receiver (or `this`): call to a method on the caller's own type.
    if receiver_text is None or receiver_text == "this":
        if ctx.caller_type_fqn in index.internal_types:
            return ctx.caller_type_fqn, 0.95, "same-file"
        return None

    # Simple identifier receiver: could be a local var, a field, or a static
    # type reference (e.g. `SomeUtil.method()`).
    if receiver_text.isidentifier():
        declared_type = ctx.local_types.get(receiver_text) or ctx.field_types.get(receiver_text)
        if declared_type:
            resolved = index.resolve_type_name(declared_type, ctx.caller_file, ctx.caller_package)
            if resolved:
                return resolved, 0.90, "import-resolved"
            return None
        # Unknown identifier -- maybe it's itself a type name (static call).
        resolved = index.resolve_type_name(receiver_text, ctx.caller_file, ctx.caller_package)
        if resolved:
            same_file = index.type_file.get(resolved) == ctx.caller_file
            return resolved, (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")
        # Last-resort tier: raw identifier text happens to match an internal
        # simple name (mirrors bestEffortCall's dead-ish tier-3 fallback).
        matches = index.simple_name_to_fqns.get(receiver_text, [])
        if len(matches) == 1:
            return matches[0], 0.50, "global"
        return None

    # More complex receiver expression (field_access chain, cast, etc.) --
    # best-effort: does its raw text look like a type reference?
    resolved = index.resolve_type_name(receiver_text, ctx.caller_file, ctx.caller_package)
    if resolved:
        same_file = index.type_file.get(resolved) == ctx.caller_file
        return resolved, (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")
    return None
