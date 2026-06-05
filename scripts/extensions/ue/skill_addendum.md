# manyread UE extension — skill addendum

UE 资产 DSL（matlang / bplisp / animlang）专属的 skill 片段。仅当 UE 扩展启用
（manyread.json `extensions: ["ue"]`、`profile: "ue"`，或源码根附近存在 *.uproject）时
才相关；通用 SKILL.md 不含这些内容。

## Edge relations（UE 资产 DSL 图边）

| relation | meaning | use |
|---|---|---|
| `binds` / `casts` / `ref` | UE asset DSL graph edges (bplisp/animlang); matlang wires use `uses_type` | asset node-graph ("连连看") analysis |

## Commands（UE 资产 DSL）

- `/mr-validate` — pre-flight structural + schema check of a matlang/bplisp/animlang file.
- `/mr-equiv` — canonical S-expr equivalence check of a regenerated/edited DSL file vs a
  reference. Authoring guardrail: run `/mr-validate --schema` first (structural + schema),
  then `/mr-equiv` to confirm the validated candidate is semantically equivalent to the
  reference (parse-tree canonicalization catches what the enrich-graph diff cannot — a
  matlang `:a`/`:b` swap, a literal value change).
- `/mr-link-source` — asset node → the C++ class that implements it.
