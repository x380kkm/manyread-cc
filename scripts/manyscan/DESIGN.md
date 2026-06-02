# manyscan — 设计规格 (v1)

> **manyscan = 架在 manyread 之上、由用户交互驱动的"针对性结构分析层"**。
> 给定一个**具体需求/疑问**,它**真实地**(基于 manyread 的真符号/边/内容)把相关的**数据与依赖**找出来,在其上做**业务/数据/逻辑抽象**,**为模块化提取与重构决策提供支撑参考**。
> 核心铁律:**有界、按需 —— 一个疑问绝不带入整个引擎**。确定性脚本是可靠骨架,避免 AI 即兴乱查出错。

## 1. 目的与原则
- **针对性(demand-driven)**:输入是一个 seed —— 一个疑问 / 一个符号 / 一个目录 / 一个特性关键词 / "我想抽出模块 X"。manyscan 只分析与之相关的切片。
- **有界(bounded)**:从 seed 带**预算**地向外扩散(深度/规模上限 + 相关性排序),**绝不全量扫**。这是"避免一个疑问带入整个引擎"的机制。
- **真实(truthful)**:数据与依赖必须从 manyread 的真索引中找出(符号/边/import),每个节点/边带 **path:line 证据**,可核实——不臆造。
- **支撑重构(refactoring-support)**:产物是模块边界、耦合热点、依赖切点、内聚/耦合度——给用户做模块化/重构的**参考**,不替用户做决定。
- **脚本为骨架(deterministic backbone)**:重活由确定性脚本做、产出可核实的结构化数据;AI/用户在可信数据上做业务抽象解释,而非即兴查询出错。
- **不绑引擎/语言**:跨 manyread 支持的所有语言通用;**不依赖 UE**(UE/.Build.cs 顶多是未来可选适配器)。

## 2. 它补的缺口
manyread 的 `edges` 只有文件内 `contains` + 文件内解析的 `extends/implements` + 可选同文件 `references`。**没有**跨文件 import 依赖、**没有**从 seed 出发的有界切片、**没有**层级 roll-up。manyscan 正是加这三样,以 manyread 索引为底座(便宜、一致)。

## 3. 兼容策略(硬依赖 manyread)
- manyscan **以文件路径 importlib 加载 manyread 的 `lib/config.py` + `lib/db.py`**(别名导入,避免与自身 `lib` 包冲突),复用其 store 发现(`list_stores` 读 `~/.manyread/stores.json`)、`resolve_project`、schema/`connect` → **manyread 改 schema/布局,manyscan 自动跟随**,绝不另抄。
- 对 store **只读**(`file:...?mode=ro`);派生结果缓存写在 `<store>/manyscan/` 侧,绝不污染 manyread。
- **SourceAdapter 缝**:manyread 未来出新能力(UE 蓝图/材质、Unity 元数据)= 新的 file/symbol/edge 行;manyscan 通过统一适配器消费——v1 是 code 适配器,现在把缝设计好,manyread 出表时即插。

## 4. manyscan 主循环(交互模型)
```
seed(需求/符号/目录/关键词)
  → scope 解析(定位 seed 在哪些符号/文件)
  → 有界依赖扩散(带预算 BFS:沿真实依赖 import/include/edges 向外,深度&规模上限+相关性剪枝)
  → 真实切片(nodes+edges,每条带 path:line 证据)
  → roll-up / 抽象(symbol→file→dir→用户自定义模块,按请求层级折叠)
  → 重构支撑分析(内聚/耦合、fan-in/out、环、依赖切点、候选模块边界)
  → 输出(结构化 JSON + mermaid/dot/文本视图)
  ↺ 用户据此细化 scope 再迭代(交互式)
```

## 5. 架构(clean,镜像 manyread 插件)
```
W:\manyscan\
  scripts\
    scan.py                 # CLI: scan(seed→切片) | deps | abstract | analyze | export
    selftest.py             # 一键自测
    lib\
      __init__.py
      stores.py             # ✅ manyread store 只读访问(importlib 复用 manyread config/db)
      adapters\__init__.py  # SourceAdapter 接口 + code 适配器(v1)
      graph.py              # 统一 node/edge 图模型 + 算法(BFS预算扩散/topo/scc/rollup)
      deps.py               # 跨文件依赖提取(import/include/using/全局 edge 解析)
      scope.py              # seed 解析 + 有界扩散预算/相关性
      rollup.py             # 层级抽象
      analyze.py            # 重构支撑分析(内聚/耦合/切点/环)
      render.py             # JSON / mermaid / dot / text
      cache.py              # 增量缓存(key: store meta.enriched_at + mtime)
  skills\manyscan\SKILL.md  # 给 agent 的用法指引
  tests\                    # pytest 单元 + 集成(以 3dgs 已索引 store 为真实 fixture)
  references\               # 设计笔记 + 与 manyread 的 schema 契约
  README.md
```
Python 3.12,stdlib 优先,PEP723 内联,`uv run`。

## 6. v1 范围(精简核心先行)
做:**seed 有界扩散切片 + 真实跨文件依赖提取 + 层级 roll-up + 重构支撑分析 + 输出 + 真实 store 集成测试**。通用语言。SourceAdapter 缝设计到位但只实现 code 适配器。
不做:UE/.Build.cs 专用分析(降为未来可选适配器)、UE 蓝图/材质/Unity 元数据读取(那是 manyread 的活,等它出能力)。

## 7. 测试(完备)
- 单元:图算法(有界 BFS/topo/scc/rollup)、依赖提取器(小 fixture)、scope 预算、adapter 契约。
- 集成:跑真实 3dgs store —— 断言已知依赖与有界性(扩散在引擎级 store 上必须受预算约束、不爆)。
- golden:渲染器确定性输出。`uv run scripts/selftest.py` 全绿。

## 8. 构建方法
/workflow 并行撰写模块+测试与评审;/loop 自定节奏迭代 实现→`uv run tests`→评审→修,直到全绿且达标(clean / tested / usable)。
