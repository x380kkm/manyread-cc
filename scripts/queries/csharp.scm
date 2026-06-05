;; audience: internal
;; csharp
;; manyread — C# dependency-EDGE query (declarative, project-customizable).
;;
;; Symbols (class/struct/interface/enum/record + method/ctor + containment + extends/
;; implements from base_list) come from the tree-sitter WALKER (_walk_csharp). THIS
;; file declares ONLY dependency EDGES: every @dep.<relation> capture -> an edge from
;; the ENCLOSING symbol to the captured name, relation = <relation>. Override per
;; project at <root>/.manyread/queries/csharp.scm (replaces this preset).

;; --- calls: Foo(...) and obj.Method(...) ---
;; invocation_expression.function is either an identifier (free call) or a
;; member_access_expression whose `name:` is the called method identifier.
(invocation_expression function: (identifier) @dep.calls)
(invocation_expression function: (member_access_expression name: (identifier) @dep.calls))

;; --- imports: `using X;`  `using X.Y.Z;`  `using static X.Y;` ---
;; `using Alias = X.Y;` puts the alias in a `name:` field; the !name anchor captures
;; the single-segment SOURCE only (never the alias), and the multi-segment source of
;; an aliased using is still caught by the qualified_name pattern.
(using_directive !name (identifier) @dep.imports)
(using_directive (qualified_name) @dep.imports)

;; --- type usage: parameter / return / variable / object-creation type positions ---
;; predefined_type (int/string) and implicit_type (var) are intentionally NOT named,
;; so primitives / `var` never become deps (mirrors cpp skipping primitive_type).
(parameter type: (identifier) @dep.uses_type)
(parameter type: (generic_name) @dep.uses_type)
(parameter type: (qualified_name) @dep.uses_type)
(method_declaration returns: (identifier) @dep.uses_type)
(method_declaration returns: (generic_name) @dep.uses_type)
(method_declaration returns: (qualified_name) @dep.uses_type)
(variable_declaration type: (identifier) @dep.uses_type)
(variable_declaration type: (generic_name) @dep.uses_type)
(variable_declaration type: (qualified_name) @dep.uses_type)
(object_creation_expression type: (identifier) @dep.uses_type)
(object_creation_expression type: (generic_name) @dep.uses_type)
(object_creation_expression type: (qualified_name) @dep.uses_type)

;; NOTE: inheritance (`: Base, IFoo`) is emitted by the WALKER as extends/implements
;; from base_list; do NOT capture it here or it double-counts.
