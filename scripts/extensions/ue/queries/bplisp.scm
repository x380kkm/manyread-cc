;; BlueprintLisp (Blueprint EventGraph/FunctionGraph -> S-expr) — tree-shaped;
;; there is NO `connect` form, so exec/data flow comes FREE from the synthesized
;; `contains` (byte-range parenting). Validated on Tests/Regression/
;; villager_select_before_print.bplisp: def.graph=1, def.node=4, def.call=3
;; (PrintString/SpawnSystemAttached/K2_SetTimer — the `:param` type is NOT a call),
;; dep.binds=4 (Selected, returnvalue, NS_Path, returnvalue).

;; graph roots -> kind=graph; the vocabulary is the graph-creating subset of the
;; importer's authoritative top-level whitelist (BlueprintLispConverter.cpp:7933-7939):
;; event-likes (event/input-action/input-key/component-bound-event/actor-bound-event),
;; function-likes (func/function/macro), and transition-cond (AnimationTransitionGraph
;; function-graph mode). var/comment/exit/call-macro are top-level-legal but not graphs.
(list . (symbol) @def.graph (#match? @def.graph "^(event|input-action|input-key|component-bound-event|actor-bound-event|func|function|macro|transition-cond)$"))

;; control / statement nodes -> kind=node
(list . (symbol) @def.node (#match? @def.node "^(let|set|seq|branch|foreach|call|delay|cast|return|exit|switch|switch-int|switch-enum|switch-string|call-parent|call-macro|vec|rot|make-array|get-array-item|break-struct)$"))

;; A UFunction / pure-call node = a capitalized head IMMEDIATELY followed by a `:pin`
;; keyword (exclude the `None` placeholder). The `. (symbol) @_pin ^:` anchor excludes
;; `:param (Name Type)` sub-lists — their 2nd child is a Type symbol, not a `:pin` — so
;; the param type is no longer mis-captured as a call.
(list . (symbol) @def.call . (symbol) @_pin
  (#match? @def.call "^[A-Z]") (#not-match? @def.call "^None$") (#match? @_pin "^:"))

;; let/set BIND a name (the IMMEDIATE 2nd child) -> relation binds. The strict
;; anchor `@_h . name` is REQUIRED (without it the trailing :id keyword also
;; matched). Exclude :keywords from the bound name.
(list . (symbol) @_h . (symbol) @dep.binds (#match? @_h "^(let|set)$") (#not-match? @dep.binds "^:"))

;; call-parent / call-macro target (cross-graph, usually unresolved) -> relation calls
(list . (symbol) @_c . (symbol) @dep.calls (#match? @_c "^(call-parent|call-macro)$"))

;; cast target type -> relation casts
(list . (symbol) @_x . (symbol) @dep.casts (#eq? @_x "cast"))
