;; audience: internal
;; tsx
;; manyread — TSX dependency-EDGE query. The .tsx dialect uses IDENTICAL tree-sitter
;; node/field names to the `typescript` grammar, so this preset mirrors
;; typescript.scm exactly. Symbols + extends/implements come from the WALKER
;; (_walk_typescript, registered for both `typescript` and `tsx`). Override per
;; project at <root>/.manyread/queries/tsx.scm (replaces this preset).

;; --- calls: foo(...) and obj.method(...) / this.svc.method(...) ---
(call_expression function: (identifier) @dep.calls (#not-eq? @dep.calls "require"))
(call_expression function: (member_expression property: (property_identifier) @dep.calls))

;; --- imports: ES module specifier (string_fragment = bare path) + CommonJS ---
(import_statement source: (string (string_fragment) @dep.imports))
(call_expression
  function: (identifier) @_req
  arguments: (arguments (string (string_fragment) @dep.imports))
  (#eq? @_req "require"))

;; --- type usage: param/return/variable annotations + `new T()` ---
(type_annotation (type_identifier) @dep.uses_type)
(new_expression constructor: (identifier) @dep.uses_type)

;; NOTE: class extends/implements + interface extends are walker-emitted — NOT here.
