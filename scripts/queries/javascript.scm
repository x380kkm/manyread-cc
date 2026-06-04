;; manyread — JavaScript dependency-EDGE query (declarative, project-customizable).
;;
;; Symbols (class/function/method + containment + extends) come from the tree-sitter
;; WALKER (_walk_javascript). THIS file declares the dependency EDGES: every
;; @dep.<relation> capture becomes an edge from the ENCLOSING symbol to the captured
;; name, with relation = <relation>. Override per project at
;; <root>/.manyread/queries/javascript.scm (replaces this preset).
;; JavaScript is untyped -> NO uses_type (no type annotations in the grammar).

;; --- calls: foo(...) and obj.method(...) ---
;; call_expression.function is either an identifier (free call) or a
;; member_expression whose property is a property_identifier (method/chained call).
(call_expression function: (identifier) @dep.calls)
(call_expression function: (member_expression property: (property_identifier) @dep.calls))

;; --- imports: ES module `import ... from "src"` + CommonJS require("x") ---
;; Capture the string_fragment (content WITHOUT quotes) so _simplify_dep gets a clean
;; module path; the bare `string` node retains quotes and would yield garbage.
(import_statement source: (string (string_fragment) @dep.imports))
(call_expression
  function: (identifier) @_req
  arguments: (arguments (string (string_fragment) @dep.imports))
  (#eq? @_req "require"))

;; NOTE: inheritance (class C extends Base) is emitted by the tree-sitter walker as
;; `extends` (from class_heritage); do NOT also capture it here or it double-counts.
;; JavaScript has no static types so there is no uses_type. This query owns
;; calls/imports only.
