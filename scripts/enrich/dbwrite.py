from __future__ import annotations

import json

from enrich.langs import HAS_WALKER


#### 把（可能经规则变换的）契约符号行 + 边写入数据库 [@380kkm 2026-06-05] ####
def _insert_file(conn, file_id: int, lang: str, rows: list[dict],
                 edges: list[dict]) -> tuple[int, int]:
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
