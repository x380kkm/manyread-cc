# manyscan 构建计划(/loop 的清单与接口契约)

> 原则:每轮 loop 推进**一个连贯增量**(按依赖序挑下一个模块),实现要 clean(类型注解+docstring),**同时写 pytest**,跑 `uv run --python 3.12 scripts/selftest.py` 必须全绿才算完。所有结论以脚本产出为准(可核实)。

## 模块依赖序 + 接口契约(一行)
- [x] `lib/stores.py` — 只读访问 manyread store(复用其 config/db)。`list_stores()->[StoreInfo]`、`resolve(store,root)->StoreInfo`、`Store(db).counts/relation_summary/lang_summary/symbols_by_name`。**已验证。**
- [x] `lib/graph.py` — 统一图模型。`Node(id,kind,label,store,evidence{path,line},attrs)`、`Edge(src,dst,relation,evidence,weight)`、`Graph` 容器 + 算法:`bfs_bounded(seeds,expand_fn,budget)`、`toposort`、`scc`(Tarjan)、`subgraph`、`rollup(level_fn)`。纯内存、无 IO。
- [x] `lib/deps.py` — 真实依赖提取(以 store 为输入)。`file_imports(store,file_id)->[(target,evidence)]`(python import/from、cpp #include、cs using、js/ts import,正则+行号);`resolve_edge_targets(store,dst_name)->[symbol]`(全局 best-effort 名称解析,带歧义计数);`symbol_file(store,sid)`。
- [x] `lib/scope.py` — seed 解析 + 有界扩散。`resolve_seed(store,seed)->[Node]`(符号名/文件/目录/关键词→起点);`expand(store,seeds,budget=Budget(max_nodes,max_depth,direction,relations))->Graph`(带预算 BFS,相关性剪枝,**必受上限约束**)。
- [x] `lib/rollup.py` — 层级抽象。`rollup(graph, level: 'file'|'dir'|'module'|callable)->Graph`(折叠组内边,聚合跨组边权重)。
- [x] `lib/analyze.py` — 重构支撑分析(纯图)。`metrics(graph)`(fan_in/out、内聚/耦合、instability=Ce/(Ca+Ce));`cycles(graph)`(scc>1);`cut_points(graph)`(候选模块边界/桥边);`layers(graph)`(拓扑分层)。
- [x] `lib/render.py` — 输出。`to_json/to_mermaid/to_dot/to_text(graph|metrics)`,确定性(排序稳定),golden 可测。
- [x] `lib/adapters/__init__.py` — `SourceAdapter` 协议(`nodes(store)`,`edges(store)`)+ `CodeAdapter`(v1,封装 stores+deps);未来 asset/meta 适配器同接口。
- [x] `lib/cache.py` — 增量缓存。key=`store.meta('enriched_at')`+seed+budget;命中则复用派生图。写 `<store>/manyscan/`。
- [x] `scripts/scan.py` — CLI。`scan <seed> [--store/--root] [--max-nodes N --depth D --dir in|out|both --rel ...] [--level ...] [--format json|mermaid|dot|text]`、`deps`、`analyze`、`export`。
- [ ] `scripts/selftest.py` — 跑 pytest 全绿。**已建。**
- [x] `skills/manyscan/SKILL.md` — agent 用法指引(seed→有界扩散→抽象→重构参考的查询 loop;何时用、预算怎么定)。
- [x] `README.md` — 安装/用法/与 manyread 的关系/扩展缝。

## 测试要求
- 单元:每模块配 pytest;图算法用合成小图;依赖提取/scope 用合成 manyread-schema 临时 db(复用 manyread `db.init_schema`,见 tests/conftest)。
- 有界性断言:在引擎级 store 上 `expand` 必须 ≤ budget.max_nodes(防"一个疑问带入整个引擎")。
- 集成(可跳过):hub 有 store 时跑真实 store,无则 skip。
- golden:render 输出确定性。

## Definition of Done(v1)
全部模块 [x] + `uv run scripts/selftest.py` 全绿 + 一轮自评审(clean/无死代码/类型完整/docstring)+ scan CLI 能在真实 store 上跑出有界切片与重构分析 + SKILL/README 完成。
