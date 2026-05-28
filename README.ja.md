# manyread

[English](README.md) · [中文](README.zh-CN.md) · **日本語**

> **[SQL-ManyThing](https://github.com/IOchair/SQL-ManyThing)**（IOchair、MIT）を Claude Code
> プラグインとして再パッケージしたものです。中核アイデアは原典のもの——まず原典をご覧ください。
> 本リポジトリは「パッケージング」のみ。移植 / 置換 / 新規の詳細は [PROVENANCE.md](PROVENANCE.md) を参照。

大規模なソースツリーをローカル SQLite（FTS5 全文 + tree-sitter シンボル/グラフ）に索引化し、
**まず SQL で絞り込み、必要な範囲だけ抽出**することで、grep + ファイル全読みより低コストで
コードを読みます。インストール後、エージェントは読む / 探す際に**自動的に manyread を優先**します
（Edit/Write の動作は変わりません）。

## インストール

**前提：[`uv`](https://docs.astral.sh/uv/)** —— 必須。PEP 723 のインラインメタデータで Python 依存を
自動管理します（手動の `pip install` は不要）。[uv 公式サイト](https://docs.astral.sh/uv/) からインストール
してください（Windows は `winget install astral-sh.uv`）。

**プラグインのインストール**（Claude Code 内、この 2 行だけ）：

```text
/plugin marketplace add x380kkm/manyread-cc
/plugin install manyread@manyread
```

**テスト**：新しいセッションで任意のリポジトリにて「このプロジェクトは何か / X はどう実装されているか」
と尋ねると、エージェントが索引作成を提案し、grep + cat ではなく manyread で回答します。

## 使い方

```text
/mr-init     フォルダを選び（既定 ./manyread）索引を作成
/mr-enrich   tree-sitter のシンボル / 呼び出しグラフを追加
/mr-query    コードを問い合わせ（manyread が既定の読み取り経路に）
/mr-ref      読解用ワークスペースの作成 / 剪定 / 注釈
/mr-rules    プロジェクト単位の解析補正ルール（例：UE の *_API マクロ）
```

データはリポジトリ内の可視フォルダ `manyread/` に置かれ（コミット可）、`user/`（個人設定）と
`short/`（短期・バージョン依存）は gitignore 対象です——コミット後に `short/` は手動で整理できます。

## ライセンス

MIT。**SQL-ManyThing**（MIT、IOchair）に由来 — <https://github.com/IOchair/SQL-ManyThing>。
[PROVENANCE.md](PROVENANCE.md) を参照。
