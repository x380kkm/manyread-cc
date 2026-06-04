;; BlueprintLisp (Blueprint EventGraph/FunctionGraph -> S-expr) — tree-shaped;
;; there is NO `connect` form, so exec/data flow comes FREE from the synthesized
;; `contains` (byte-range parenting). Validated on Tests/Regression/
;; villager_select_before_print.bplisp: def.graph=1, def.node=4, def.call=4,
;; dep.binds=4 (Selected, returnvalue, NS_Path, returnvalue).

;; graph roots -> kind=graph
(list . (symbol) @def.graph (#match? @def.graph "^(event|func|function|macro)$"))

;; control / statement nodes -> kind=node
(list . (symbol) @def.node (#match? @def.node "^(let|set|seq|branch|foreach|call|delay|cast|return|exit|switch|switch-int|switch-enum|switch-string|call-parent|call-macro|vec|rot|make-array|get-array-item|break-struct)$"))

;; capitalized head = a UFunction / pure-call node -> kind=call (exclude the
;; `None` placeholder). KNOWN benign extra: `:param (Selected Actor)` makes
;; `Selected` a spurious def.call (head of a param sub-list); accepted as noise.
(list . (symbol) @def.call (#match? @def.call "^[A-Z]") (#not-match? @def.call "^None$"))

;; let/set BIND a name (the IMMEDIATE 2nd child) -> relation binds. The strict
;; anchor `@_h . name` is REQUIRED (without it the trailing :id keyword also
;; matched). Exclude :keywords from the bound name.
(list . (symbol) @_h . (symbol) @dep.binds (#match? @_h "^(let|set)$") (#not-match? @dep.binds "^:"))

;; call-parent / call-macro target (cross-graph, usually unresolved) -> relation calls
(list . (symbol) @_c . (symbol) @dep.calls (#match? @_c "^(call-parent|call-macro)$"))

;; cast target type -> relation casts
(list . (symbol) @_x . (symbol) @dep.casts (#eq? @_x "cast"))
