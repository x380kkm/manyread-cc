;; MatLang (UMaterial -> S-expr) — symbols + wires for a material DAG.
;; Scheme grammar: every (...) is a `list`; head/keyword/$id are `symbol`; "..."
;; is a `string` (text INCLUDES the quotes); the captured `@def` token's symbol
;; span is its enclosing `list` ancestor (see _query_symbols). All patterns were
;; compiled + run against DSL/Examples/*.matlang (simple_pbr / emissive_rim).

;; material top-level: name = the quoted string after `material`.
(list . (symbol) @_m (string) @def.material (#eq? @_m "material"))

;; expression node: a list inside (expressions ...); @def lands on the $id (2nd
;; symbol), node TYPE = 1st symbol (promoted to attrs.node_type by the builder).
(list . (symbol) @_e
  (list . (symbol) @_type (symbol) @def.node (#match? @def.node "^[$]"))
  (#eq? @_e "expressions"))

;; outputs block: a def so the 4 material-output wires (which fall outside every
;; node span) have an enclosing symbol to attribute to.
(list . (symbol) @def.outputs (#eq? @def.outputs "outputs"))

;; wire: (connect $id idx?) -> emit as `uses_type` so the manyscan boundary REL
;; gate (extends/implements/uses_type) picks it up unchanged. The node symbol
;; keeps its '$' (=='$mul1'); the dep dst_name '$mul1' resolves to it by-name.
(list . (symbol) @_c (symbol) @dep.uses_type (#eq? @_c "connect"))
