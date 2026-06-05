;; audience: internal
;; typescript
;; manyread — TypeScript dependency-EDGE query (declarative, project-customizable).
;;
;; Symbols (class/interface/enum/type/function/method + containment + extends/
;; implements) come from the tree-sitter WALKER (_walk_typescript). THIS file
;; declares the dependency EDGES: every @dep.<relation> capture becomes an edge
;; from the ENCLOSING symbol to the captured name, with relation = <relation>.
;; Override per project at <root>/.manyread/queries/typescript.scm (full replace).
;; Node/field names below are grounded in tree-sitter-typescript (the .ts grammar;
;; .tsx routes through the sibling tsx.scm, same node names).
;; Supported relations: calls | imports | uses_type (extends/implements stay
;; walker-owned to avoid double-count).

;; --- calls: foo(...) and obj.method(...) / this.svc.method(...) ---
;; A plain identifier call; `require` is excluded — it is captured as an import
;; below (CommonJS), so counting it as a call too would double-count.
(call_expression function: (identifier) @dep.calls (#not-eq? @dep.calls "require"))
;; A member call: only the final .property is the callee name (matches python's
;; `attribute: (identifier)` convention — the receiver chain is not a dep).
(call_expression function: (member_expression property: (property_identifier) @dep.calls))

;; --- imports: ES module specifier + CommonJS require ---
;; `import ... from "x"` / `export ... from "x"`: capture the inner string_fragment
;; (the bare module path WITHOUT the surrounding quotes) so _simplify_dep gets a
;; clean specifier (a quoted `string` node text would include the '"').
(import_statement source: (string (string_fragment) @dep.imports))
;; CommonJS: const m = require("x");
(call_expression
  function: (identifier) @_req
  arguments: (arguments (string (string_fragment) @dep.imports))
  (#eq? @_req "require"))

;; --- type usage: parameter / return / variable type annotations + `new T()` ---
;; A type_annotation wraps the type in `: T` positions (param/return/var); capture
;; the named type_identifier only (predefined_type like number/void/string is a
;; builtin and is intentionally NOT captured, mirroring python skipping primitives).
(type_annotation (type_identifier) @dep.uses_type)
;; Construction `new Widget()` is a use of the constructed type.
(new_expression constructor: (identifier) @dep.uses_type)

;; NOTE: class `extends`/`implements` and interface `extends` are emitted by the
;; tree-sitter walker (class_heritage / extends_type_clause). Do NOT capture them
;; here or they double-count. This query owns calls/imports/uses_type only.
