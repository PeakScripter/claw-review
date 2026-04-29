"""Translate raw mypy diagnostics into human-readable Finding fields.

mypy speaks in terms of its internal type model. This module reframes those
messages as reviewer-facing findings that explain *why* the issue matters and
*what* to do about it — not just what mypy detected.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from review_agent.findings.model import Category, Severity


class Translation(NamedTuple):
    title: str
    rationale: str
    severity: Severity
    category: Category


# --------------------------------------------------------------------------- #
# Error-code baseline severity                                                 #
# --------------------------------------------------------------------------- #

# Hygiene/annotation issues that don't risk runtime failures.
_LOW_CODES = frozenset({
    "no-untyped-def", "no-untyped-call", "no-untyped-return",
    "import-untyped", "import-not-found", "import",
    "annotation-unchecked", "misc", "type-arg",
    "no-any-explicit", "no-any-unimported",
    "redundant-cast", "redundant-expr", "tautological-compare",
    "override",  # signature mismatches are usually caught before runtime
})

# Issues that almost always mean a NameError / ImportError at runtime.
_HIGH_CODES = frozenset({
    "name-defined",
    "module-attr",
    "call-arg",      # wrong arg count → TypeError at runtime
    "call-overload",
})


def _code_severity(code: str | None) -> Severity:
    if code is None:
        return "medium"
    if code in _LOW_CODES:
        return "low"
    if code in _HIGH_CODES:
        return "high"
    return "medium"


def _code_category(code: str | None) -> Category:
    if code in {
        "no-untyped-def", "no-untyped-call", "no-untyped-return",
        "import-untyped", "import-not-found", "annotation-unchecked",
    }:
        return "style"
    return "correctness"


# --------------------------------------------------------------------------- #
# Compiled message patterns                                                    #
# --------------------------------------------------------------------------- #

_ARG_TYPE = re.compile(
    r'Argument (\d+) to "(.+?)" has incompatible type "(.+?)"; expected "(.+?)"'
)
_KWARG_TYPE = re.compile(
    r'Argument "(.+?)" to "(.+?)" has incompatible type "(.+?)"; expected "(.+?)"'
)
_ASSIGNMENT = re.compile(
    r'Incompatible types in assignment \(expression has type "(.+?)", variable has type "(.+?)"\)'
)
_NONE_ATTR = re.compile(
    r'Item "None" of "(.+?)" has no attribute "(.+?)"'
)
_ATTR = re.compile(r'"(.+?)" has no attribute "(.+?)"')
_RETURN = re.compile(
    r'Incompatible return value type \(got "(.+?)", expected "(.+?)"\)'
)
_OVERRIDE_RETURN = re.compile(
    r'Return type "(.+?)" of "(.+?)" incompatible with return type "(.+?)" in supertype "(.+?)"'
)
_NAME_UNDEF = re.compile(r'Name "(.+?)" is not defined')
_MODULE_ATTR = re.compile(r'Module "(.+?)" has no attribute "(.+?)"')
_TOO_MANY_ARGS = re.compile(r'Too many arguments for "(.+?)"')
_UNEXPECTED_KW = re.compile(r'Unexpected keyword argument "(.+?)" for "(.+?)"')
_MISSING_RETURN = re.compile(r'Missing return statement')
_STUBS_NOT_INSTALLED = re.compile(r'Library stubs not installed for "(.+?)"')
_CANNOT_FIND_MODULE = re.compile(
    r'Cannot find implementation or library stub for module named "(.+?)"'
)
_PROTOCOL_COMPAT = re.compile(
    r'Incompatible types in "(.+?)" \(expression has type "(.+?)", '
    r'base class "(.+?)" defined the type as "(.+?)"\)'
)


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def translate(message: str, code: str | None) -> Translation:
    """Return a human-readable Translation for one mypy diagnostic."""

    if m := _ARG_TYPE.search(message):
        arg_n, func, got, want = m.group(1), m.group(2), m.group(3), m.group(4)
        return Translation(
            title=f"Wrong argument type passed to `{func}`",
            rationale=(
                f"Argument {arg_n} of `{func}` expects `{want}` but received `{got}`. "
                f"This mismatch may cause a runtime `TypeError` or silent data corruption "
                f"if the function assumes the argument satisfies `{want}`."
            ),
            severity="medium",
            category="correctness",
        )

    if m := _KWARG_TYPE.search(message):
        kw, func, got, want = m.group(1), m.group(2), m.group(3), m.group(4)
        return Translation(
            title=f"Wrong type for keyword argument `{kw}` in `{func}`",
            rationale=(
                f"The keyword argument `{kw}` passed to `{func}` is `{got}` "
                f"but `{want}` is required."
            ),
            severity="medium",
            category="correctness",
        )

    if m := _ASSIGNMENT.search(message):
        got, want = m.group(1), m.group(2)
        return Translation(
            title=f"Incompatible assignment: `{got}` into `{want}` variable",
            rationale=(
                f"A value of type `{got}` is assigned to a variable declared as `{want}`. "
                f"Code that reads this variable later and assumes it holds `{want}` may "
                f"fail or produce incorrect results."
            ),
            severity="medium",
            category="correctness",
        )

    # None-dereference is more specific than generic attr-defined — check first.
    if m := _NONE_ATTR.search(message):
        union_type, attr = m.group(1), m.group(2)
        return Translation(
            title=f"Possible None dereference: `.{attr}` without a None guard",
            rationale=(
                f"The value is typed as `{union_type}`, meaning it may be `None` at this "
                f"point. Accessing `.{attr}` without checking for `None` first will raise "
                f"`AttributeError` at runtime whenever the value is absent. "
                f"Add an explicit `is not None` check before this access."
            ),
            severity="high",
            category="correctness",
        )

    if m := _ATTR.search(message):
        typ, attr = m.group(1), m.group(2)
        return Translation(
            title=f"Accessing undefined attribute `.{attr}` on `{typ}`",
            rationale=(
                f"The type `{typ}` has no attribute `.{attr}`. "
                f"This will raise `AttributeError` at runtime."
            ),
            severity="medium",
            category="correctness",
        )

    if m := _RETURN.search(message):
        got, want = m.group(1), m.group(2)
        return Translation(
            title=f"Return type mismatch: returns `{got}`, declared `{want}`",
            rationale=(
                f"The function returns `{got}` but its signature declares `{want}`. "
                f"Callers that rely on the declared return type may behave incorrectly."
            ),
            severity="medium",
            category="correctness",
        )

    if m := _OVERRIDE_RETURN.search(message):
        got, method, want, base = m.group(1), m.group(2), m.group(3), m.group(4)
        return Translation(
            title=f"Override of `{method}` breaks `{base}` contract",
            rationale=(
                f"The overriding method `{method}` returns `{got}`, but the base class "
                f"`{base}` declares it returns `{want}`. Code typed as `{base}` that calls "
                f"`{method}()` may receive the wrong type, violating the Liskov "
                f"Substitution Principle."
            ),
            severity="medium",
            category="correctness",
        )

    if m := _NAME_UNDEF.search(message):
        name = m.group(1)
        return Translation(
            title=f"Undefined name `{name}`",
            rationale=(
                f"`{name}` is referenced but not defined in this scope. "
                f"This raises `NameError` at runtime."
            ),
            severity="high",
            category="correctness",
        )

    if m := _MODULE_ATTR.search(message):
        mod, attr = m.group(1), m.group(2)
        return Translation(
            title=f"Non-existent attribute `{attr}` imported from `{mod}`",
            rationale=(
                f"The module `{mod}` does not export `{attr}`. "
                f"This raises `ImportError` at runtime."
            ),
            severity="high",
            category="correctness",
        )

    if m := _TOO_MANY_ARGS.search(message):
        func = m.group(1)
        return Translation(
            title=f"Too many arguments passed to `{func}`",
            rationale=(
                f"`{func}` is called with more arguments than its signature accepts. "
                f"This raises `TypeError` at runtime."
            ),
            severity="high",
            category="correctness",
        )

    if m := _UNEXPECTED_KW.search(message):
        kw, func = m.group(1), m.group(2)
        return Translation(
            title=f"Unexpected keyword argument `{kw}` for `{func}`",
            rationale=(
                f"`{func}` does not accept a keyword argument named `{kw}`. "
                f"This raises `TypeError` at runtime."
            ),
            severity="high",
            category="correctness",
        )

    if _MISSING_RETURN.search(message):
        return Translation(
            title="Function may return implicitly (implicit `None` return)",
            rationale=(
                "Not all code paths return a value. If the caller expects a non-None "
                "return, it will receive `None` and may fail silently or raise later."
            ),
            severity="medium",
            category="correctness",
        )

    if m := _STUBS_NOT_INSTALLED.search(message):
        pkg = m.group(1)
        return Translation(
            title=f"No type stubs for `{pkg}` — calls are unchecked",
            rationale=(
                f"mypy cannot verify calls into `{pkg}` because it has no type stubs. "
                f"Type errors in this library's API will go undetected. "
                f"Install `{pkg}-stubs` (or `types-{pkg}`) to restore coverage. "
                f"This is a hygiene issue, not a runtime bug."
            ),
            severity="low",
            category="style",
        )

    if m := _CANNOT_FIND_MODULE.search(message):
        mod = m.group(1)
        return Translation(
            title=f"Import `{mod}` not resolvable by mypy",
            rationale=(
                f"mypy cannot find the module `{mod}`. "
                f"If this is a valid third-party package, install its stubs. "
                f"If the import path is wrong, fix it — an unresolvable import creates "
                f"a blind spot where real type errors go undetected."
            ),
            severity="low",
            category="style",
        )

    if m := _PROTOCOL_COMPAT.search(message):
        field, got, base, want = m.group(1), m.group(2), m.group(3), m.group(4)
        return Translation(
            title=f"Protocol violation: `{field}` type `{got}` conflicts with `{base}`",
            rationale=(
                f"The base class `{base}` declares `{field}` as `{want}`, but this "
                f"class sets it to `{got}`. This breaks the Liskov Substitution Principle "
                f"and may cause type errors in code that uses the base-class interface."
            ),
            severity="medium",
            category="correctness",
        )

    return _fallback(message, code)


def _fallback(message: str, code: str | None) -> Translation:
    sev = _code_severity(code)
    cat = _code_category(code)
    label = f"[{code}]" if code else "type error"
    clean = message.strip()
    return Translation(
        title=f"Type {label}: {clean[:150]}",
        rationale=clean,
        severity=sev,
        category=cat,
    )
