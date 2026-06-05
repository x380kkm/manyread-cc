;; audience: internal
;; python
;; manyread — Python dependency-EDGE query (declarative, project-customizable).
;;
;; Symbols (class/function/method + containment) come from the tree-sitter walker.
;; THIS file declares the dependency EDGES: every @dep.<relation> capture becomes an
;; edge from the ENCLOSING symbol to the captured name, with relation = <relation>.
;; Override per project at <root>/.manyread/queries/python.scm (replaces this preset).
;; Supported relations: calls | imports | uses_type | extends (any name after @dep. works).

;; --- calls: foo(...) and obj.method(...) ---
(call function: (identifier) @dep.calls)
(call function: (attribute attribute: (identifier) @dep.calls))

;; --- imports: `import a.b`, `import a.b as c`, `from a.b import c` ---
(import_statement name: (dotted_name) @dep.imports)
(import_statement name: (aliased_import (dotted_name) @dep.imports))
(import_from_statement module_name: (dotted_name) @dep.imports)
(import_from_statement module_name: (relative_import (dotted_name) @dep.imports))

;; --- type usage: parameter / return / variable annotations ---
(typed_parameter type: (type) @dep.uses_type)
(typed_default_parameter type: (type) @dep.uses_type)
(function_definition return_type: (type) @dep.uses_type)
(assignment type: (type) @dep.uses_type)

;; NOTE: inheritance (class C(Base):) is emitted by the tree-sitter walker as `extends`;
;; do NOT also capture it here or it double-counts. This query owns calls/imports/uses_type.
