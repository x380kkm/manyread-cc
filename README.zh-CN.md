# manyread

[English](README.md) · **中文** · [日本語](README.ja.md)

> 基于 **[SQL-ManyThing](https://github.com/IOchair/SQL-ManyThing)**（IOchair，MIT）再包装成 Claude Code
> 插件。核心思想来自原项目，建议先看它；本仓库只做「包装」。改造细节见 [PROVENANCE.md](PROVENANCE.md)。

把大型源码树建成本地 SQLite 索引（FTS5 全文 + tree-sitter 符号/调用图），让 AI 用
**「先 SQL 缩小范围，再有界提取」** 的方式低成本读代码，而不是 grep + 整文件硬读。
装好后 agent 会**自动优先用它来读 / 搜代码**（不改变 Edit/Write 改代码的方式）。

## 安装

**前置：[`uv`](https://docs.astral.sh/uv/)** —— 必需。它按 PEP 723 内联元数据自动管理 Python 依赖，
无需手动 `pip install`。安装见 [uv 官网](https://docs.astral.sh/uv/)（Windows 也可
`winget install astral-sh.uv`）。

**安装插件**（在 Claude Code 里，就这两条）：

```text
/plugin marketplace add x380kkm/manyread-cc
/plugin install manyread@manyread
```

**测试**：新开一个会话，在任意代码仓库里问「这个项目是什么 / X 是怎么实现的」——
agent 会主动提议建索引，然后用 manyread 回答（而不是 grep + cat）。

## 用法

```text
/mr-init     选个文件夹（默认 ./manyread）建索引
/mr-enrich   加 tree-sitter 符号 / 调用图
/mr-query    查询代码（manyread 成为默认读代码方式）
/mr-ref      建 / 裁剪 / 标注一个阅读工作区
/mr-rules    项目级解析修正规则（如修 UE 的 *_API 宏）
```

索引等数据放在仓库内可见的 `manyread/` 文件夹里（可提交共享）；`user/`（个人配置）和
`short/`（短期、版本相关）默认 gitignore，提交后可手动清理 `short/`。

## 许可

MIT。源自 **SQL-ManyThing**（MIT，IOchair）— <https://github.com/IOchair/SQL-ManyThing>。
见 [PROVENANCE.md](PROVENANCE.md)。
