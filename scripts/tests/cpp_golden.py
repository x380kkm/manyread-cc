# audience: internal
# tests.cpp_golden
"""cpp 遍历器提取的 golden 夹具：一份源码与其期望的 (rows, edges)。

由依赖边端到端套件（test_enrich_query.py）与宏剥离套件（test_macro_strip.py）共用：
前者校验遍历器逐字节产出此 golden，后者校验默认开启的宏剥离对这份无修饰符宏的 golden 是 no-op。
"""

#### cpp golden 源码：含继承、成员、字段类型、自由函数 [@380kkm 2026-06-05] ####
CPP_GOLDEN_SRC = (
    "class Foo : public Base {\n"
    "  Widget w;\n"
    "  Out compute(Arg a) { return helper(a); }\n"
    "};\n"
    "void freefn(Thing t) {}\n"
)

#### cpp golden 期望符号行（类 Foo、成员 compute、自由函数 freefn） [@380kkm 2026-06-05] ####
CPP_GOLDEN_ROWS = [
    {"_local": 0, "file_id": 1, "name": "Foo", "kind": "class", "lang": "cpp",
     "start_line": 1, "end_line": 4, "start_byte": 0, "end_byte": 82,
     "parent_local": None, "attrs": {}, "provenance": []},
    {"_local": 1, "file_id": 1, "name": "compute", "kind": "function", "lang": "cpp",
     "start_line": 3, "end_line": 3, "start_byte": 40, "end_byte": 80,
     "parent_local": 0, "attrs": {}, "provenance": []},
    {"_local": 2, "file_id": 1, "name": "freefn", "kind": "function", "lang": "cpp",
     "start_line": 5, "end_line": 5, "start_byte": 84, "end_byte": 107,
     "parent_local": None, "attrs": {}, "provenance": []},
]

#### cpp golden 期望依赖边（contains/extends/uses_type） [@380kkm 2026-06-05] ####
CPP_GOLDEN_EDGES = [
    {"file_id": 1, "src_local": 0, "dst_local": 1, "dst_name": "compute", "relation": "contains"},
    {"file_id": 1, "src_local": 0, "dst_local": None, "dst_name": "Base", "relation": "extends"},
    {"file_id": 1, "src_local": 0, "dst_local": None, "dst_name": "Widget", "relation": "uses_type"},
    {"file_id": 1, "src_local": 1, "dst_local": None, "dst_name": "Out", "relation": "uses_type"},
    {"file_id": 1, "src_local": 1, "dst_local": None, "dst_name": "Arg", "relation": "uses_type"},
    {"file_id": 1, "src_local": 2, "dst_local": None, "dst_name": "Thing", "relation": "uses_type"},
]
