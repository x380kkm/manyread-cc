from __future__ import annotations

import re

# 在类型位置出现、几乎可以确定是被 tree-sitter 误读成类型的 C/C++ 宏（没有预处理器
# 运行）：全大写带下划线的形式可命中 UE 的 export/DSL 宏（UE_API、ENGINE_API、*_API、
# SHADER_PARAMETER、BEGIN_SHADER_PARAMETER_STRUCT…）；少量 EXTRA 集合命中不带下划线的
# 函数说明符宏。刻意不匹配全大写无下划线的形式，使 GUID / HRESULT / UINT 等真实类型存活。
_MACRO_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_MACRO_TYPE_EXTRA = frozenset({"FORCEINLINE", "FORCENOINLINE", "FORCEINLINE_DEBUGGABLE", "CONSTEXPR"})


#### 判断一个 token 是否为应被剥离的宏类型名 [@380kkm 2026-06-05] ####
def _is_macro_type(name: str) -> bool:
    return name in _MACRO_TYPE_EXTRA or bool(_MACRO_TYPE_RE.match(name))


# 长度保持的、解析前的声明修饰符宏剥离（c 系语言）：
# tree-sitter-cpp 会错误解析 `class <ALLCAPS_MACRO> <RealName> ...`（export/可见性/弃用
# 宏，如 ENGINE_API / BASE_EXPORT / PROTOBUF_EXPORT / CV_EXPORTS / UE_DEPRECATED(5.0)）：
# 它把宏当成类名，并把真实名 + 基类列表 + 主体重新归入一个 ERROR 节点，于是真实名以及
# 所有成员/方法全部丢失（root_node.has_error 变为 True，或名字被悄悄解析错）。把宏 token
# （及其后任何成对的 `(...)` 实参）用相同字节数空白化（保留换行）后，类便能以真实名 + 真实
# 主体正确解析，且每个存活 token 都保持原始字节偏移 / 行号。
#
# 仅当宏之后还跟着第二个标识符（真实名）时才触发：`class RGBA {}`（无第二个标识符）会保留
# RGBA 作为类名，不受影响。堆叠的宏（`class DLL_EXPORT ENGINE_API UMaterial {}`）通过把
# 单趟剥离迭代到不动点来完全恢复（每趟把领头的宏空白化 -> 空白，重扫便看到下一个处于修饰符
# 位置的宏）。空白化只作用于喂给 parser.parse() 的本地副本；入库的 DB 内容保持原样
# （长度保持 => 所有输出的 span 对未改动内容仍然有效）。
#
# 默认宏识别器是生产用的 `_is_macro_type`（复用，而非另写一套正则），可按项目经由
# manyread.json macro_strip.extra_names（字面 token，如基础正则漏掉的尾下划线 GTEST_API_）
# / extra_patterns（以 OR 并入的正则）扩展。仅对 lang=="cpp" 运行（涵盖路由到 cpp 的 HLSL
# 扩展名）。
_DECL_MACRO_RE = re.compile(
    # 分组 1：关键字 + 空白（原样保留）；领头的 \b 阻止 class/struct 作为用户标识符
    # （subclass、metaclass、mystruct、superclass）的子串被命中。`enum class <MACRO>
    # <Name>` 仍会触发：\b 在空白之后的 class 词首处匹配，从而恢复 enum 的真实名。
    r"(\b(?:class|struct)\s+)"
    # 分组 2：候选宏 token
    r"([A-Za-z_][A-Za-z0-9_]*)"
    # 分组 3：可选的单个成对 (...) 实参 + 空白（被空白化）
    r"(\s*(?:\([^()]*\))?\s+)"
    # 分组 4：真实名 —— 必须紧跟一个第二标识符
    r"([A-Za-z_][A-Za-z0-9_]*)"
)

_CFAMILY_STRIP_LANGS = frozenset({"cpp"})


#### 长度保持地空白化一段字符串（按 UTF-8 字节数补空格，换行原样保留） [@380kkm 2026-06-05] ####
def _blank_preserving(s: str) -> str:
    """把被剥离的 span 空白化，使其经 `.encode("utf-8")` 后字节长度与行结构都与原文相同
    —— 下游每个 start_byte/end_byte/start_line/end_line 都保持精确。

    起决定作用的不变量是字节长度而非字符长度：内容在 parser.parse() 前会重新编码为
    UTF-8。被空白化区域内的非 ASCII 字符（如 UE_DEPRECATED(5.0, "Use Foo — instead")
    消息串里的 em-dash）占多个 UTF-8 字节，若塌缩成一个空格会缩短字节长度并移位宏之后
    的每个 span。因此对每个字符按其 UTF-8 字节数各输出一个空格（换行原样保留 —— 它是 1
    字节且能维持行结构）。
    """
    return "".join(
        "\n" if c == "\n" else " " * len(c.encode("utf-8")) for c in s
    )


#### 构造 is_macro(token) 判定函数（内建检测器 OR 配置扩展） [@380kkm 2026-06-05] ####
def _macro_strip_predicate(macro_strip: dict):
    """构造 is_macro(token) 判定：内建的 `_is_macro_type` 检测器，OR 上配置里的
    extra_names（字面量）+ extra_patterns（已编译正则）。"""
    extra_names = frozenset(macro_strip.get("extra_names") or ())
    extra_pats = [re.compile(p) for p in (macro_strip.get("extra_patterns") or ())]

    #### 判定单个 token 是否应作为宏被剥离 [@380kkm 2026-06-05] ####
    def is_macro(tok: str) -> bool:
        return (_is_macro_type(tok) or tok in extra_names
                or any(p.match(tok) for p in extra_pats))

    return is_macro


#### 单趟剥离 `class|struct <MACRO> <RealName>` 位置上的宏 token [@380kkm 2026-06-05] ####
def _strip_decl_macros_once(content: str, is_macro) -> tuple[str, bool]:
    """单趟：空白化每个处于 `class|struct <MACRO> <RealName>` 位置的宏 token。
    返回 (new_content, changed)。按 `_blank_preserving` 长度保持。
    """
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


#### 长度保持地迭代剥离声明修饰符宏到不动点 [@380kkm 2026-06-05] ####
def _strip_decl_macros(content: str, macro_strip: dict | None) -> str:
    """对 `class|struct <MACRO> <RealName>` 位置的声明修饰符宏做纯函数式、确定性、长度
    保持的剥离。当变换被禁用（macro_strip 为 None 或 enabled=false）或无任何匹配（干净
    的 cpp 是字节级 no-op）时返回未改动的内容。幂等：对已空白化的输出再跑一次找不到任何
    匹配。

    堆叠的修饰符宏（`class DLL_EXPORT ENGINE_API UMaterial {}`，export+可见性/属性宏组合
    时常见）会被完全恢复：本趟空白化第一个宏，把它变成空白，于是重扫现在看到的是
    `class <第二个宏> <RealName>` 并把它也剥离。我们迭代到不动点（每趟有变化后重扫）。每趟
    至少空白化一个 token，且只会把宏 token 变成空白（绝不加长 / 绝不触碰真实名），故循环严格
    缩小宏 token 集合并终止；首趟之后即干净的情形多花一次 no-op 扫描。`_PASS_LIMIT` 上限是
    防范任何病态情形的双保险。
    """
    # 仅在 None 或显式 enabled=false 时禁用。`{}`/部分字典应遵循 enabled 的默认值
    # （True）：`{}` 为假值，但 `{}.get("enabled", True)` 为 True，故若按 `not macro_strip`
    # 守卫会悄悄禁用空配置，违背默认开启的意图。（真实流水线里 config.load_macro_strip
    # 总返回完整填充的默认值，因此这只对直接构造部分字典的调用方有意义 —— 但守卫现已一致。）
    if macro_strip is None or not macro_strip.get("enabled", True):
        return content
    is_macro = _macro_strip_predicate(macro_strip)
    _PASS_LIMIT = 64
    for _ in range(_PASS_LIMIT):
        content, changed = _strip_decl_macros_once(content, is_macro)
        if not changed:
            break
    return content
