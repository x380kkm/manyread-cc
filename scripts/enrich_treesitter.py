# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter>=0.23", "tree-sitter-language-pack"]
# ///
"""manyread L2 —— 基于 tree-sitter 的 symbol/edge 富化。

读取一个 project 的 <root>/.manyread/source.db 中的 ``files`` 表，按语言用
tree-sitter 解析每个文件，填充 ``symbols`` 与 ``edges`` 表：

  * symbols：name、kind、lang、精确的起止行 + 字节、parent_id（按词法嵌套表达
    containment）。
  * edges：``contains``（parent -> child）、``extends``/``implements``（来自基类
    子句 / heritage），以及可选的尽力 ``references``（--refs）。

Grammar 来源：所有 grammar 均来自单个 ``tree-sitter-language-pack`` wheel（300+
语言），经 get_language() 取得；它返回标准 tree_sitter Language，因此标准 Parser
（bytes 输入、``children`` 属性）驱动下面每个 walker。新增一种语言 = 映射其扩展名 +
pack 名 + 一个小 walker。

Languages：cpp、python、javascript、typescript、csharp、glsl、java、gdscript。
  - Java (.java) 用 java grammar：class/interface/enum/record + method/
    constructor；superclass -> extends，interfaces -> implements。
  - GDScript (.gd, Godot) 用 gdscript grammar：class_name + 内部类，函数
    （嵌套在 class 下时记为 method）。
  - TypeScript (.ts) / TSX (.tsx) 用 tree-sitter-typescript：class、interface、
    enum、type alias、function、method、arrow const、extends/implements。
    （.ts 与 .tsx 成对：请求 "typescript" 覆盖两套 grammar。）
  - GLSL (.glsl/.vert/.frag/.comp/.geom/.tesc/.tese) 用 tree-sitter-glsl：
    function + struct（类 C；无继承）。
  - C# (.cs) 用 tree-sitter-c-sharp：class/struct/interface/enum + method/
    constructor 声明，containment 经嵌套表达，基类型 -> extends。
  - HLSL / 类 shader 扩展名（.hlsl .cginc .usf .ush .compute .fx .shader）经 cpp
    grammar 走*尽力的类 C 解析*。ShaderLab ``.shader`` 文件内嵌 HLSL 块，所以 cpp
    grammar 对它们只产出部分的 function/struct 符号；结果按近似看待。
  - 对 cpp 还会把 ``preproc_ifdef`` / ``preproc_if``（及其 #elif/#else 分支）记为
    kind 为 ``ifdef_branch`` 的符号，使 prune 层（ref strip-ifdef）能机械地切掉
    不匹配的 span。

原始 tree-sitter 抽取之后，有一个可选的 project 级 OVERRIDE-RULES pass
（spec 第 16 节），用于纠正 codebase 特有的写法（例如 Unreal 导出宏被误读成类名）。
规则存在 <root>/.manyread/rules.json，经 rules.py 中的纯引擎施加；符号会获得
``attrs``（json）+ ``provenance``（json）。无规则文件（且无 --rules）-> 与基础行为
完全一致（向后兼容）。

Idempotent：先清空已有的 ``symbols``/``edges`` 再重填（全量重建）。写入
meta(enriched_at, enrich_langs)。打印逐语言的 symbol/edge 计数。

CLI：  enrich_treesitter.py <alias|--root PATH> [--langs cpp,python,csharp] [--refs]
                           [--rules PATH] [--no-rules] [--rules-preview]

关于 grammar：tree-sitter-language-pack 的 get_language(name) 返回现成的
tree_sitter.Language（不是 capsule），所以 Parser(get_language(name)) +
parser.parse(bytes) 是受支持的路径。该 pack 钉住了它自己的 tree-sitter；不要同时再钉
单独的 ``tree-sitter-<lang>`` wheel（它们会争抢 binding）。

THIN FACADE（Phase-1 cleancode 拆分）：实现现在落在 ``enrich`` 包里
（enrich/model.py、langreg.py、macro_strip.py、langs/*、query.py、extract.py、
dbwrite.py、rules_glue.py、pipeline.py）。本模块重新导出完整公开表面，使
``import enrich_treesitter as E; E.<name>`` 与 ``from enrich_treesitter import <name>``
保持原样可用，并保留 ``main()`` + ``__main__`` 入口供 ``uv run enrich_treesitter.py``。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# 把 scripts/ 放到 sys.path 最前（先于 import enrich.*），使做
# from lib import config, db / import rules 的包模块能解析，且使 enrich 可作为顶层
# 包被导入。机制与拆分前的模块完全一致（它在 from lib import config, db 之前插入此行）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config, db
# 同级模块：纯 override-rules 引擎 + 加载器（spec 第 16 节）
import rules

# 重新导出第三方 tree-sitter 表面（langreg 是 wrapper 边界）
from tree_sitter import Language, Node, Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language

#### 从 enrich 包重新导出完整公开表面 [@380kkm 2026-06-05] ####
from enrich.langreg import (LANG_FOR_EXT, SUPPORTED_LANGS, _PACK_NAME,
                            _load_language)
from enrich.model import (Pending, SymRow, _named_child_text, _text)
from enrich.macro_strip import (_CFAMILY_STRIP_LANGS, _DECL_MACRO_RE,
                                _MACRO_TYPE_EXTRA, _MACRO_TYPE_RE,
                                _blank_preserving, _is_macro_type,
                                _macro_strip_predicate, _strip_decl_macros,
                                _strip_decl_macros_once)
from enrich.langs.cpp import (_CPP_DEFS, _CPP_PREPROC, _collect_type_idents,
                              _cpp_declarator_name, _cpp_function_type_idents,
                              _cpp_ifdef_label, _cpp_name, _walk_cpp)
from enrich.langs.python import _PY_DEFS, _walk_python
from enrich.langs.javascript import _js_lexical_fn_name, _walk_javascript
from enrich.langs.csharp import (_CS_CALLABLE_DEFS, _CS_TYPE_DEFS,
                                 _CS_TYPE_KINDS, _walk_csharp)
from enrich.langs.typescript import _TS_TYPE_DEFS, _walk_typescript
from enrich.langs.glsl import _GLSL_DEFS, _walk_glsl
from enrich.langs.java import (_JAVA_CALLABLE, _JAVA_TYPE_DEFS,
                               _JAVA_TYPE_KINDS, _java_type_names, _walk_java)
from enrich.langs.gdscript import _gd_first_ident, _walk_gdscript
from enrich.langs import HAS_WALKER, WALKERS
from enrich.query import (_QUERY_DIR, _dsl_list_ancestor, _dsl_name,
                          _load_query_specs, _query_edges, _query_symbols,
                          _simplify_dep)
from enrich.extract import _extract_file, _reference_edges
from enrich.dbwrite import _insert_file
from enrich.rules_glue import (_default_rules_path, _preview_diff,
                               _resolve_merged_rules)
from enrich.pipeline import enrich, main
#### /从 enrich 包重新导出公开表面 ####


if __name__ == "__main__":
    raise SystemExit(main())
