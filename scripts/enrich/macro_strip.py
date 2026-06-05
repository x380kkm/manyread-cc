# audience: internal
# enrich.macro_strip
from __future__ import annotations

import re

# 匹配全大写带下划线的宏类型名
_MACRO_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_MACRO_TYPE_EXTRA = frozenset({"FORCEINLINE", "FORCENOINLINE", "FORCEINLINE_DEBUGGABLE", "CONSTEXPR"})


#### 判断一个 token 是否为应被剥离的宏类型名 [@380kkm 2026-06-05] ####
def _is_macro_type(name: str) -> bool:
    return name in _MACRO_TYPE_EXTRA or bool(_MACRO_TYPE_RE.match(name))
#### /判断一个 token 是否为应被剥离的宏类型名 ####


# 匹配 `class|struct <MACRO> <RealName>` 位置上的声明修饰符宏
_DECL_MACRO_RE = re.compile(
    # 分组 1：关键字 + 空白（原样保留）
    r"(\b(?:class|struct)\s+)"
    # 分组 2：候选宏 token
    r"([A-Za-z_][A-Za-z0-9_]*)"
    # 分组 3：可选的单个成对 (...) 实参 + 空白
    r"(\s*(?:\([^()]*\))?\s+)"
    # 分组 4：真实名
    r"([A-Za-z_][A-Za-z0-9_]*)"
)

_CFAMILY_STRIP_LANGS = frozenset({"cpp"})


#### 长度保持地空白化一段字符串（按 UTF-8 字节数补空格，换行原样保留） [@380kkm 2026-06-05] ####
def _blank_preserving(s: str) -> str:
    return "".join(
        "\n" if c == "\n" else " " * len(c.encode("utf-8")) for c in s
    )
#### /长度保持地空白化一段字符串 ####


#### 构造 is_macro(token) 判定函数（内建检测器 OR 配置扩展） [@380kkm 2026-06-05] ####
def _macro_strip_predicate(macro_strip: dict):
    extra_names = frozenset(macro_strip.get("extra_names") or ())
    extra_pats = [re.compile(p) for p in (macro_strip.get("extra_patterns") or ())]

    #### 判定单个 token 是否应作为宏被剥离 [@380kkm 2026-06-05] ####
    def is_macro(tok: str) -> bool:
        return (_is_macro_type(tok) or tok in extra_names
                or any(p.match(tok) for p in extra_pats))
    #### /判定单个 token 是否应作为宏被剥离 ####

    return is_macro
#### /构造 is_macro(token) 判定函数 ####


#### 单趟剥离 `class|struct <MACRO> <RealName>` 位置上的宏 token [@380kkm 2026-06-05] ####
def _strip_decl_macros_once(content: str, is_macro) -> tuple[str, bool]:
    out: list[str] = []
    pos = 0
    for m in _DECL_MACRO_RE.finditer(content):
        if not is_macro(m.group(2)):
            # group2 是真实名（如 RGBA）-> 不处理
            continue
        out.append(content[pos:m.start(2)])
        # 空白化 [宏 token 起点, 真实名起点)：宏 + 任何 (...) 实参 + 空白
        out.append(_blank_preserving(content[m.start(2):m.start(4)]))
        # group4（真实名）+ 主体原样保留
        pos = m.start(4)
    if not out:
        # 未触发剥离 -> 字节级不变
        return content, False
    out.append(content[pos:])
    return "".join(out), True
#### /单趟剥离 `class|struct <MACRO> <RealName>` 位置上的宏 token ####


#### 长度保持地迭代剥离声明修饰符宏到不动点 [@380kkm 2026-06-05] ####
def _strip_decl_macros(content: str, macro_strip: dict | None) -> str:
    # macro_strip 为 None 或 enabled=false 时禁用变换
    if macro_strip is None or not macro_strip.get("enabled", True):
        return content
    is_macro = _macro_strip_predicate(macro_strip)
    _PASS_LIMIT = 64
    for _ in range(_PASS_LIMIT):
        content, changed = _strip_decl_macros_once(content, is_macro)
        if not changed:
            break
    return content
#### /长度保持地迭代剥离声明修饰符宏到不动点 ####
