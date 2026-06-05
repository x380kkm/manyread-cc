"""UE 资产 DSL 扩展 —— manyread 的可选、UE 项目级领域插件。

把三种 UE 资产 DSL（matlang / bplisp / animlang）的全部专属能力接到通用核心上：
语言与文法路由、默认扩展名预设、各自的 .scm 依赖边查询、语义 schema 目录、结构 +
语义校验 pass，以及 /mr-validate、/mr-link-source 两条命令文档。挂入分两个钩子：
``register_ingest`` 是纯 stdlib 的摄取面（L1 索引器无 tree-sitter 也能跑），
``register_enrich`` 在体内延迟 import 后接入 enrich 世界；通用核心不含任何 UE 字符串。

三种 DSL 都是 S 表达式文本、复用 tree-sitter 的 ``scheme`` 文法、且没有 walker，符号与
边完全来自各自的 .scm 查询。pass 的注册顺序与 v0.8.16 完全一致，以保证输出逐字节不变。
"""
from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent


#### 把 UE 扩展的纯 stdlib 摄取能力接到核心注册表上 [@380kkm 2026-06-05] ####
def register_ingest(reg) -> None:
    # 默认扩展名预设
    reg.add_default_exts("matlang", [".matlang"])
    reg.add_default_exts("bplisp", [".bplisp"])
    reg.add_default_exts("animlang", [".animlang"])

    # 语义 schema 目录 + 命令文档
    reg.add_schema_dir(_HERE / "schemas")
    reg.add_command(_HERE / "commands" / "mr-validate.md")
    reg.add_command(_HERE / "commands" / "mr-link-source.md")


#### 把 UE 资产 DSL 的语言、查询与校验 pass 接到 enrich 世界 [@380kkm 2026-06-05] ####
def register_enrich(reg) -> None:
    # 体内延迟 import：这两个模块（经 enrich）依赖 tree-sitter
    from dsl_validate import pass_parse
    from extensions.ue.validate_passes import (pass_animlang_required,
                                               pass_bplisp_required,
                                               pass_external_warn,
                                               pass_matlang_cycle,
                                               pass_matlang_dangling,
                                               pass_matlang_dup_id,
                                               pass_matlang_required,
                                               pass_semantic_schema)

    # 语言 + 文法路由（三种 DSL 都用 scheme 文法）
    reg.add_lang(".matlang", "matlang", grammar="scheme")
    reg.add_lang(".bplisp", "bplisp", grammar="scheme")
    reg.add_lang(".animlang", "animlang", grammar="scheme")

    # .scm 查询目录
    reg.add_scm_dir(_HERE / "queries")

    # 校验 pass（顺序须与 v0.8.16 完全一致）
    reg.register_passes(
        "matlang",
        structural=[pass_parse, pass_matlang_required, pass_matlang_dup_id,
                    pass_matlang_dangling, pass_matlang_cycle],
        semantic=[pass_semantic_schema],
    )
    reg.register_passes(
        "bplisp",
        structural=[pass_parse, pass_bplisp_required, pass_external_warn],
        semantic=[],
    )
    reg.register_passes(
        "animlang",
        structural=[pass_parse, pass_animlang_required, pass_external_warn],
        semantic=[],
    )
