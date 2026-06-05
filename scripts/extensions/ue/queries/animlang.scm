;; audience: internal
;; animlang
;; AnimLang (AnimBP -> S-expr) — pose tree + cached-pose DAG. The pose tree comes
;; FREE from the synthesized `contains` (byte-range parenting). Validated on
;; DSL/Examples/*.animlang (state_machine def.node=12, third_person_char=33,
;; simple_blend=4; all has_error=False).
;;
;; NOTE: (define ...) and (ref ...) are EXPORTER-form (authoritative plugin
;; output) and are ABSENT from the in-repo samples — the def.binding / dep.ref
;; patterns below are validated only against a SYNTHETIC snippet, not repo data.
;; Re-verify them against a real exporter dump before relying on (ref ...)/(define
;; ...) resolution.

;; pose/state node = a list whose head is NOT a keyword / structural head /
;; operator / variable type-tag.
(list . (symbol) @def.node
  (#not-match? @def.node "^:")
  (#not-match? @def.node "^[<>=+*/-]")
  (#not-match? @def.node "^(define|ref|asset|and|or|not|if|float|int|bool|vector|rotator|transform|name|enum|object)$"))

;; cached-pose binding (exporter form): (define Identifier body) -> kind=binding,
;; name = 2nd symbol.
(list . (symbol) @_d . (symbol) @def.binding (#eq? @_d "define"))

;; external data wire (exporter form): (ref "Title") -> relation ref. Mostly
;; cross-graph -> stays unresolved. dst_name keeps the quotes (the reused
;; _query_edges runs _simplify_dep, NOT _dsl_name, so a `string` dep target is
;; '"Title"' verbatim).
(list . (symbol) @_r (string) @dep.ref (#eq? @_r "ref"))
