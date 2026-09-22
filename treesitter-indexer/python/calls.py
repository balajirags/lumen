"""
CALLS edge resolution with confidence tiers for Python, closing three
documented, fixture-proven gaps in indexer/parsers/python/parse.py's
CALLS resolution (verified by direct read of that file):

  0.95 "same-file"       -- `self.method()` inside a class, OR a bare call to
                             a free function defined in the caller's own
                             module, OR `obj.method()` where `obj` was
                             directly constructed in the SAME file
                             (`obj = ClassName(...)`) and `ClassName` is
                             internal.
  0.90 "import-resolved"  -- `import x; x.foo()` for an internal module `x`
                             (the old engine explicitly skips this case);
                             `from x import y; y()` for an internal function
                             `y`; `obj.method()` where `obj`'s constructor
                             came from an internal class imported from a
                             DIFFERENT file.
  0.50 "global"           -- does not exist in the old engine at all. Same-
                             corpus name index: the called method/function's
                             own NAME (not the receiver) matches exactly one
                             function/method anywhere in the parsed corpus.
                             Flagged as the single highest-leverage addition,
                             since Python's dynamic typing makes local type
                             inference fail far more often than in Java.

Deliberately syntactic, not real symbol resolution -- same accepted
simplification java/calls.py documents. Matches by (module/class, method
name) only; there is no argument-count/type overload resolution to match
against in Python's call syntax anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepoIndex:
    """Repo-wide facts extractor.py builds during Pass 1, consumed here."""

    internal_modules: set[str] = field(default_factory=set)
    internal_classes: set[str] = field(default_factory=set)
    class_file: dict[str, str] = field(default_factory=dict)          # class fqn -> file
    module_file: dict[str, str] = field(default_factory=dict)         # module dotted name -> file
    module_functions: dict[str, set[str]] = field(default_factory=dict)  # module -> {funcSimpleName}
    class_methods: dict[str, dict[str, str]] = field(default_factory=dict)  # class fqn -> {methodName: methodId}
    function_ids: dict[str, str] = field(default_factory=dict)        # "module.func" -> functionId
    # simple callee name (method OR free function) -> [fqn, ...] across the
    # whole corpus -- used only by the 0.50 global fallback tier.
    simple_callable_to_fqns: dict[str, list[str]] = field(default_factory=dict)

    def resolve_module(self, dotted: str) -> str | None:
        return dotted if dotted in self.internal_modules else None

    def resolve_class_in_module(self, module: str, simple_name: str) -> str | None:
        candidate = f"{module}.{simple_name}"
        return candidate if candidate in self.internal_classes else None

    def resolve_function_in_module(self, module: str, simple_name: str) -> str | None:
        if simple_name in self.module_functions.get(module, ()):
            return self.function_ids.get(f"{module}.{simple_name}")
        return None


@dataclass
class ImportedName:
    kind: str  # "module" | "class" | "function"
    target: str  # dotted module name, or "module.Class"/"module.func"


@dataclass
class MethodCallContext:
    """Per-call-site facts extractor.py gathers while walking one function/method body."""

    caller_module: str
    caller_class: str | None  # class fqn, or None for a module-level function
    caller_file: str
    imports: dict[str, ImportedName]           # local name -> resolved import
    local_var_classes: dict[str, str]          # varName -> class fqn, from `var = ClassName(...)` in this scope


def method_id(class_fqn: str, method_name: str) -> str:
    """Shared ID scheme, also used by extractor.py when creating Method nodes
    -- guarantees resolve_call_target's lookups agree with node creation."""
    return f"method:{class_fqn}#{method_name}"


def function_id(module: str, func_name: str) -> str:
    """Shared ID scheme, also used by extractor.py when creating Function nodes."""
    return f"function:{module}.{func_name}"


def resolve_call_target(
    receiver_text: str | None,
    method_name: str,
    ctx: MethodCallContext,
    index: RepoIndex,
) -> tuple[str, float, str] | None:
    """Returns (target_id, confidence, reason) or None if unresolvable/external.

    receiver_text is None for a bare call (`helper()`), "self" for a
    self-scoped call, or the raw receiver expression text otherwise
    (`obj.method()`, `notifications.send()`, `self.repo.save()` -- only the
    single-identifier receiver shapes below are resolved; a multi-hop chain
    like `self.repo.save()` falls through to the 0.50 tier, same as Java's
    equivalent "complex receiver expression" fallback).
    """

    if receiver_text is None:
        target = index.resolve_function_in_module(ctx.caller_module, method_name)
        if target:
            return target, 0.95, "same-file"
        imported = ctx.imports.get(method_name)
        if imported and imported.kind == "function":
            module, _, func = imported.target.rpartition(".")
            target = index.resolve_function_in_module(module, func)
            if target:
                return target, 0.90, "import-resolved"
        return _global_fallback(method_name, index)

    if receiver_text == "self":
        if ctx.caller_class:
            target = index.class_methods.get(ctx.caller_class, {}).get(method_name)
            if target:
                return target, 0.95, "same-file"
        return _global_fallback(method_name, index)

    if receiver_text.isidentifier():
        declared_class = ctx.local_var_classes.get(receiver_text)
        if declared_class:
            target = index.class_methods.get(declared_class, {}).get(method_name)
            if target:
                same_file = index.class_file.get(declared_class) == ctx.caller_file
                return target, (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")

        imported = ctx.imports.get(receiver_text)
        if imported:
            if imported.kind == "module":
                target = index.resolve_function_in_module(imported.target, method_name)
                if target:
                    return target, 0.90, "import-resolved"
            elif imported.kind == "class":
                target = index.class_methods.get(imported.target, {}).get(method_name)
                if target:
                    return target, 0.90, "import-resolved"

    return _global_fallback(method_name, index)


def _global_fallback(method_name: str, index: RepoIndex) -> tuple[str, float, str] | None:
    matches = index.simple_callable_to_fqns.get(method_name, [])
    if len(matches) == 1:
        return matches[0], 0.50, "global"
    return None
