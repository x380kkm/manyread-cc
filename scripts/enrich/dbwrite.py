from __future__ import annotations

import json

from enrich.langs import HAS_WALKER


# --- DB write ----------------------------------------------------------------
def _insert_file(conn, file_id: int, lang: str, rows: list[dict],
                 edges: list[dict]) -> tuple[int, int]:
    """Insert (possibly rule-transformed) contract rows+edges into the DB.

    Resolves edge endpoints from `_local` indices to assigned DB ids. `extends`/
    `implements` edges with dst_local=None are matched to a same-file type symbol
    by name (best-effort), using the POST-transform names. Returns (n_sym, n_edge).
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
             None,  # parent_id wired below once all ids are known
             attrs_json, prov_json),
        )
        local_to_db[row["_local"]] = cur.lastrowid

    # Wire parent_id now that every row has a db id.
    for row in rows:
        parent_local = row.get("parent_local")
        if parent_local is not None and parent_local in local_to_db:
            conn.execute(
                "UPDATE symbols SET parent_id=? WHERE id=?",
                (local_to_db[parent_local], local_to_db[row["_local"]]),
            )

    # Name -> db id for type symbols (resolve inheritance targets in-file). For a
    # walker-less DSL, WIDEN this to ALL symbols so an asset wire ((connect $mul1)
    # / (ref ...)) resolves to its in-file node symbol by name; cpp/python keep the
    # exact class/struct/interface resolvable set (byte-identical). Names are
    # assumed UNIQUE within a DSL file (true for matlang $ids); duplicate names in
    # animlang resolve first-wins by (start_byte,end_byte) order — deterministic
    # because _query_symbols emits rows in a total sort order.
    is_dsl = lang not in HAS_WALKER
    name_to_id: dict[str, int] = {}
    for row in rows:
        if is_dsl or row.get("kind") in ("class", "struct", "interface"):
            name_to_id.setdefault(row.get("name"), local_to_db[row["_local"]])

    n_edges = 0
    for e in edges:
        src_local = e.get("src_local")
        if src_local not in local_to_db:
            continue  # source dropped by a rule.
        src_id = local_to_db[src_local]
        dst_local = e.get("dst_local")
        dst_name = e.get("dst_name")
        if dst_local is not None:
            if dst_local not in local_to_db:
                continue  # target dropped by a rule.
            dst_id = local_to_db[dst_local]
        else:
            dst_id = name_to_id.get(dst_name)
        conn.execute(
            "INSERT INTO edges(file_id, src_symbol_id, dst_symbol_id, dst_name, relation) "
            "VALUES(?,?,?,?,?)",
            (file_id, src_id, dst_id, dst_name, e.get("relation")),
        )
        n_edges += 1

    return len(rows), n_edges
