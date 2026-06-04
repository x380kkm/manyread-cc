from __future__ import annotations

import json

from enrich.langs import HAS_WALKER


#### 把（可能经规则变换的）契约符号行 + 边写入数据库 [@380kkm 2026-06-05] ####
def _insert_file(conn, file_id: int, lang: str, rows: list[dict],
                 edges: list[dict]) -> tuple[int, int]:
    """把契约 rows + edges 入库，边端点从 `_local` 下标解析为分配的 DB id。

    dst_local=None 的 `extends`/`implements` 边按名匹配到同文件的类型符号
    （尽力而为），用变换后的名字。返回 (n_sym, n_edge)。
    """
    local_to_db: dict[int, int] = {}
    for row in rows:
        attrs = row.get("attrs") or {}
        prov = row.get("provenance") or []
        attrs_json = json.dumps(attrs) if attrs else None
        prov_json = json.dumps(prov) if prov else None
        cur = conn.execute(
            "INSERT INTO symbols(file_id, name, kind, lang, start_line, end_line, "
            "start_byte, end_byte, parent_id, attrs, provenance) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (file_id, row.get("name"), row.get("kind"), row.get("lang") or lang,
             row.get("start_line"), row.get("end_line"),
             row.get("start_byte"), row.get("end_byte"),
             # parent_id 待全部 id 已知后在下方回填
             None,
             attrs_json, prov_json),
        )
        local_to_db[row["_local"]] = cur.lastrowid

    #### 每行都有 db id 后回填 parent_id [@380kkm 2026-06-05] ####
    for row in rows:
        parent_local = row.get("parent_local")
        if parent_local is not None and parent_local in local_to_db:
            conn.execute(
                "UPDATE symbols SET parent_id=? WHERE id=?",
                (local_to_db[parent_local], local_to_db[row["_local"]]),
            )
    #### /回填 parent_id ####

    #### 建名字 -> db id 表，用于按名解析同文件的边目标 [@380kkm 2026-06-05] ####
    # 对类型符号解析继承目标。无 walker 的 DSL 把范围 WIDEN 到所有符号，使资产
    # 连线（(connect $mul1) / (ref ...)）按名解析到同文件的 node 符号；cpp/python
    # 仍只保留可解析的 class/struct/interface 集合（字节级一致）。名字在一个 DSL
    # 文件内假定 UNIQUE（matlang $id 成立）；animlang 重名按 (start_byte,end_byte)
    # 顺序首个胜出 —— 确定性，因为 _query_symbols 以全序产出行。
    is_dsl = lang not in HAS_WALKER
    name_to_id: dict[str, int] = {}
    for row in rows:
        if is_dsl or row.get("kind") in ("class", "struct", "interface"):
            name_to_id.setdefault(row.get("name"), local_to_db[row["_local"]])
    #### /名字到 db id 表 ####

    #### 解析每条边的端点并写入 edges 表 [@380kkm 2026-06-05] ####
    n_edges = 0
    for e in edges:
        src_local = e.get("src_local")
        # 源被规则丢弃
        if src_local not in local_to_db:
            continue
        src_id = local_to_db[src_local]
        dst_local = e.get("dst_local")
        dst_name = e.get("dst_name")
        if dst_local is not None:
            # 目标被规则丢弃
            if dst_local not in local_to_db:
                continue
            dst_id = local_to_db[dst_local]
        else:
            dst_id = name_to_id.get(dst_name)
        conn.execute(
            "INSERT INTO edges(file_id, src_symbol_id, dst_symbol_id, dst_name, relation) "
            "VALUES(?,?,?,?,?)",
            (file_id, src_id, dst_id, dst_name, e.get("relation")),
        )
        n_edges += 1
    #### /解析并写入边 ####

    return len(rows), n_edges
#### /把符号行与边写入数据库 ####
