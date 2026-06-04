from __future__ import annotations

import re

# tokens that, in a TYPE position, are almost certainly a C/C++ MACRO mis-read as a
# type by tree-sitter (no preprocessor runs): ALL-CAPS-WITH-UNDERSCORE catches the UE
# export/DSL macros (UE_API, ENGINE_API, *_API, SHADER_PARAMETER,
# BEGIN_SHADER_PARAMETER_STRUCT, …); the small EXTRA set catches the underscore-free
# function-specifier macros. Deliberately does NOT match all-caps-no-underscore, so
# real types like GUID / HRESULT / UINT survive.
_MACRO_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_MACRO_TYPE_EXTRA = frozenset({"FORCEINLINE", "FORCENOINLINE", "FORCEINLINE_DEBUGGABLE", "CONSTEXPR"})


def _is_macro_type(name: str) -> bool:
    return name in _MACRO_TYPE_EXTRA or bool(_MACRO_TYPE_RE.match(name))


# --- length-preserving pre-parse declaration-modifier macro strip (c-family) -
# Tree-sitter-cpp mis-parses `class <ALLCAPS_MACRO> <RealName> ...` (export/
# visibility/deprecation macros like ENGINE_API / BASE_EXPORT / PROTOBUF_EXPORT /
# CV_EXPORTS / UE_DEPRECATED(5.0)): it takes the MACRO as the class name and re-homes
# the real name + base list + BODY into an ERROR node, so the real name AND all
# members/methods are LOST (root_node.has_error becomes True, or the name is silently
# wrong). Blanking the macro token (and any trailing balanced `(...)` args) with the
# SAME number of bytes — newlines kept — makes the class parse correctly with the real
# name + a real body, and EVERY surviving token keeps its ORIGINAL byte offset / line.
#
# Only fires when a SECOND identifier (the real name) follows the macro: `class RGBA {}`
# (no second ident) leaves RGBA as the name, untouched. STACKED macros (`class
# DLL_EXPORT ENGINE_API UMaterial {}`) are fully recovered by iterating the single-pass
# strip to a fixed point (each pass blanks the leading macro -> whitespace, the re-scan
# then sees the next macro in modifier position). The blank is applied ONLY to the local
# copy fed to parser.parse(); the stored DB content stays original (length-preserving =>
# all emitted spans remain valid against the unmodified content).
#
# The default macro detector is the production `_is_macro_type` (REUSED, not a divergent
# regex), extended per-project via manyread.json macro_strip.extra_names (literal tokens,
# e.g. trailing-underscore GTEST_API_ that the base regex misses) / extra_patterns
# (regexes OR'd in). Run ONLY for lang=="cpp" (covers HLSL exts, which route to cpp).
#
# Regex groups: 1=keyword+ws (kept), 2=candidate macro token (filtered by is_macro),
# 3=optional single balanced (...) args + ws (BLANKED with the macro), 4=the REAL name
# (a SECOND identifier MUST follow, else no match => byte-identical).
_DECL_MACRO_RE = re.compile(
    r"(\b(?:class|struct)\s+)"        # 1: keyword + ws  (KEPT verbatim). The leading
                                       #    \b stops `class`/`struct` matching as a
                                       #    SUBSTRING of a user identifier (subclass,
                                       #    metaclass, mystruct, superclass) and so
                                       #    blanking arbitrary in-identifier source.
                                       #    `enum class <MACRO> <Name>` still fires: \b
                                       #    matches at the `class` word start after the
                                       #    space, recovering the enum's real name.
    r"([A-Za-z_][A-Za-z0-9_]*)"       # 2: candidate macro token
    r"(\s*(?:\([^()]*\))?\s+)"        # 3: optional single balanced (...) args + ws (BLANKED)
    r"([A-Za-z_][A-Za-z0-9_]*)"       # 4: the REAL name — a SECOND identifier MUST follow
)

_CFAMILY_STRIP_LANGS = frozenset({"cpp"})


def _blank_preserving(s: str) -> str:
    """Blank a stripped span so that, after `.encode("utf-8")`, the result has the
    SAME BYTE LENGTH and the same line structure as the original — every downstream
    start_byte/end_byte/start_line/end_line stays exact.

    BYTE-length, not char-length, is the load-bearing invariant: the content is
    re-encoded to UTF-8 before parser.parse(). A non-ASCII char inside the blanked
    region (e.g. an em-dash in a UE_DEPRECATED(5.0, "Use Foo — instead") message
    string) is multiple UTF-8 bytes, so collapsing it to ONE space would shrink the
    byte length and shift every span after the macro. We therefore emit one space per
    UTF-8 byte of each char (newlines kept verbatim — they are 1 byte and preserve the
    line structure)."""
    return "".join(
        "\n" if c == "\n" else " " * len(c.encode("utf-8")) for c in s
    )


def _macro_strip_predicate(macro_strip: dict):
    """Build the is_macro(token) predicate: the built-in `_is_macro_type` detector
    OR'd with config extra_names (literal) + extra_patterns (compiled regexes)."""
    extra_names = frozenset(macro_strip.get("extra_names") or ())
    extra_pats = [re.compile(p) for p in (macro_strip.get("extra_patterns") or ())]

    def is_macro(tok: str) -> bool:
        return (_is_macro_type(tok) or tok in extra_names
                or any(p.match(tok) for p in extra_pats))

    return is_macro


def _strip_decl_macros_once(content: str, is_macro) -> tuple[str, bool]:
    """ONE pass: blank every macro token in a `class|struct <MACRO> <RealName>`
    position. Returns (new_content, changed). LENGTH-PRESERVING per `_blank_preserving`.
    """
    out: list[str] = []
    pos = 0
    for m in _DECL_MACRO_RE.finditer(content):
        if not is_macro(m.group(2)):
            continue                      # group2 is a real name (e.g. RGBA) -> untouched
        out.append(content[pos:m.start(2)])
        # blank [macro token start, real-name start): the macro + any (...) args + ws
        out.append(_blank_preserving(content[m.start(2):m.start(4)]))
        pos = m.start(4)                  # group4 (real name) + BODY kept verbatim
    if not out:
        return content, False             # no strip fired -> byte-identical
    out.append(content[pos:])
    return "".join(out), True


def _strip_decl_macros(content: str, macro_strip: dict | None) -> str:
    """PURE, deterministic, LENGTH-PRESERVING strip of declaration-modifier macros in
    the `class|struct <MACRO> <RealName>` position. Returns content UNCHANGED when the
    transform is disabled (macro_strip None or enabled=false) or nothing matches (clean
    cpp is a byte-identical no-op). Idempotent: re-running on blanked output finds none.

    STACKED modifier macros (`class DLL_EXPORT ENGINE_API UMaterial {}`, common with
    export+visibility/attribute macros) are fully recovered: the pass blanks the FIRST
    macro, which turns it into whitespace, so a re-scan now sees `class <2nd-macro>
    <RealName>` and strips that too. We iterate to a FIXED POINT (re-scan after each
    changing pass). Each pass blanks >=1 token and only ever turns macro tokens into
    whitespace (never lengthens / never touches the real name), so the loop strictly
    shrinks the set of macro tokens and terminates; a clean-after-first pass costs one
    extra no-op scan. The `_PASS_LIMIT` cap is belt-and-suspenders against any pathology.
    """
    # Disable ONLY on None or an explicit enabled=false. A `{}`/partial dict respects
    # the enabled-default (True): `{}` is falsy but `{}.get("enabled", True)` is True,
    # so guarding on `not macro_strip` would silently disable an empty config and
    # contradict the default-ON intent. (In the real pipeline config.load_macro_strip
    # always returns the fully-populated DEFAULT, so this only matters for direct
    # callers that construct a partial dict — but the guard is now consistent.)
    if macro_strip is None or not macro_strip.get("enabled", True):
        return content
    is_macro = _macro_strip_predicate(macro_strip)
    _PASS_LIMIT = 64
    for _ in range(_PASS_LIMIT):
        content, changed = _strip_decl_macros_once(content, is_macro)
        if not changed:
            break
    return content
