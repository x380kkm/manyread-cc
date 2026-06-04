"""Tests for the pre-flight STRUCTURAL DSL validator (scripts/dsl_validate.py).

Lives beside test_enrich_query.py (NOT in scripts/manyscan/tests) for the SAME
sys.path reason: dsl_validate imports enrich_treesitter which uses the manyread
`lib` package, which would shadow manyscan's own `lib` in that suite's sys.path.

Run from the scripts/ dir WITH the tree-sitter deps, e.g.:
    cd scripts && uv run --python 3.12 --with pytest --with "tree-sitter>=0.23" \
        --with tree-sitter-language-pack -m pytest tests/test_dsl_validate.py -q

GOOD fixtures are inline mirrors of the real reference files (the same constants
test_enrich_query.py uses) so the suite is self-contained + path-independent; an
optional pass reads the real W:/cc/reference files only if present (skip-if-absent).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))  # scripts/
try:
    import dsl_validate as V
    _HAVE = True
except Exception:  # noqa: BLE001 - skip cleanly when tree-sitter isn't installed
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="tree-sitter not installed")


# --- GOOD fixtures (inline mirrors of reference/*; expect zero errors) -------
_GOOD_MATLANG = (
    '(material "M_SimplePBR"\n'
    "  :domain surface\n"
    "  (expressions\n"
    "    (texture-sample $tex1 :uv (connect $uv1))\n"
    "    (texture-coordinate $uv1 :coordinate-index 0)\n"
    '    (vector-parameter $vparam1 :name "TintColor")\n'
    "    (multiply $mul1 :a (connect $tex1 0) :b (connect $vparam1 0))\n"
    "    (constant $const1 :value 0.0))\n"
    "  (outputs\n"
    "    :base-color (connect $mul1 0)\n"
    "    :metallic (connect $const1 0)))\n"
)

_GOOD_BPLISP = (
    "(function\n"
    "  None\n"
    '  :event-id "8abce957"\n'
    "  :param (Selected Actor)\n"
    '  (PrintString :instring "Villager Select called!" :id "5f6936c3")\n'
    '  (set Selected "K2Node_FunctionEntry" :id "226de0c6")\n'
    "  (let returnvalue\n"
    '    (SpawnSystemAttached :location "0, 0, 0" :id "60944b57")))\n'
)

_GOOD_ANIMLANG = (
    '(anim-blueprint "SimpleStateMachine"\n'
    "  :variables [(float :speed 0.0 :range [0.0 600.0])]\n"
    "  :anim-graph\n"
    "    (state-machine :locomotion :initial :idle\n"
    "      :states\n"
    '        [(state :idle (sequence-player "Idle_Rifle" :loop true))\n'
    '         (state :walk (sequence-player "Walk_Fwd" :loop true))]))\n'
)


def _codes(text, lang, sev=None):
    return sorted(i.code for i in V.dsl_validate(text, lang)
                  if sev is None or i.severity == sev)


# --- GOOD -> zero errors per DSL ---------------------------------------------
def test_good_matlang_zero_errors():
    assert _codes(_GOOD_MATLANG, "matlang", "error") == []


def test_good_bplisp_zero_errors_warns_only():
    issues = V.dsl_validate(_GOOD_BPLISP, "bplisp")
    assert [i for i in issues if i.severity == "error"] == []
    # unresolved binds (Selected/returnvalue) are WARNINGS, never errors.
    assert any(i.code == "UNRESOLVED_REF" and i.severity == "warning" for i in issues)


def test_good_animlang_zero_errors():
    assert _codes(_GOOD_ANIMLANG, "animlang", "error") == []


# --- crafted BAD matlang fixtures (each caught with the right code) ----------
def test_dangling_wire():
    bad = ('(material "M" (expressions (multiply $m1 :a (connect $missing 0)))'
           " (outputs :base-color (connect $m1 0)))")
    assert "DANGLING_WIRE" in _codes(bad, "matlang", "error")


def test_duplicate_id():
    bad = ('(material "M" (expressions (constant $c1 :value 1.0)'
           " (multiply $c1 :a (connect $c1 0))) (outputs :base-color (connect $c1 0)))")
    codes = _codes(bad, "matlang", "error")
    assert "DUP_ID" in codes
    # regression: a duplicate id that also self-wires must NOT emit a phantom CYCLE.
    # The cycle graph collapses BY NAME and is ambiguous under a duplicate id, so
    # pass_matlang_cycle skips when any id repeats (DUP_ID already flags it).
    assert "CYCLE" not in codes


def test_cycle():
    bad = ('(material "M" (expressions (multiply $a :x (connect $b 0))'
           " (multiply $b :x (connect $a 0))) (outputs :base-color (connect $a 0)))")
    codes = _codes(bad, "matlang", "error")
    assert "CYCLE" in codes


def test_cycle_three_node_and_self_loop():
    # 3-node cycle $a->$b->$c->$a -> exactly one CYCLE (locks in graph.scc behavior
    # beyond the 2-node case). All ids unique, so the cycle pass is not skipped.
    three = ('(material "M" (expressions (multiply $a :x (connect $b 0))'
             " (multiply $b :x (connect $c 0)) (multiply $c :x (connect $a 0)))"
             " (outputs :base-color (connect $a 0)))")
    issues = V.dsl_validate(three, "matlang")
    cycles = [i for i in issues if i.code == "CYCLE"]
    assert len(cycles) == 1
    # a self-loop (connect $a) inside $a is a 1-node cycle caught by the self-loop
    # filter (graph.scc returns singletons; only self-looped ones are real cycles).
    loop = ('(material "M" (expressions (multiply $a :x (connect $a 0)))'
            " (outputs :base-color (connect $a 0)))")
    assert "CYCLE" in _codes(loop, "matlang", "error")


def test_no_material_root():
    assert "MATLANG_NO_MATERIAL" in _codes("(expressions (constant $c1 :value 1.0))",
                                           "matlang", "error")


def test_no_outputs_block():
    # a material with no (outputs ...) -> MATLANG_NO_OUTPUTS error.
    assert "MATLANG_NO_OUTPUTS" in _codes('(material "M" (expressions (constant $c1 :value 1.0)))',
                                          "matlang", "error")


def test_parse_error():
    # unbalanced paren -> the grammar rejects it.
    assert "PARSE_ERROR" in _codes('(material "M" (expressions (multiply $m1', "matlang", "error")


# --- bplisp / animlang required-root errors ----------------------------------
def test_bplisp_no_graph_root():
    # bare expression, no (event|func|function|macro ...) head -> error.
    assert "BPLISP_NO_GRAPH" in _codes('(PrintString :instring "x" :id "1")', "bplisp", "error")


def test_unknown_lang():
    issues = V.dsl_validate("(material \"M\")", "klingon")
    assert [i.code for i in issues] == ["UNKNOWN_LANG"]
    assert issues[0].severity == "error"


# --- determinism + parse-error-does-not-short-circuit ------------------------
def test_deterministic():
    a = V.dsl_validate(_GOOD_MATLANG, "matlang")
    b = V.dsl_validate(_GOOD_MATLANG, "matlang")
    assert a == b


def test_parse_error_runs_other_passes():
    # a file with BOTH a parse error AND a missing material -> PARSE_ERROR is
    # present and the pipeline does not short-circuit (other passes still run).
    bad = "(expressions (multiply $m1 :a (connect $x"
    issues = V.dsl_validate(bad, "matlang")
    codes = {i.code for i in issues}
    assert "PARSE_ERROR" in codes
    # all issues sorted by (byte, code, message) -> deterministic ordering.
    assert issues == sorted(issues, key=lambda i: i.sort_key())


# --- optional: validate the REAL reference files if present (skip if absent) -
_REF = r"W:\cc\reference"
_REAL = [
    (os.path.join(_REF, "MaterialBP2DSL", "DSL", "Examples", "simple_pbr.matlang"), "matlang"),
    (os.path.join(_REF, "MaterialBP2DSL", "DSL", "Examples", "emissive_rim.matlang"), "matlang"),
    (os.path.join(_REF, "Blueprint2DSL", "Tests", "Regression",
                  "villager_select_before_print.bplisp"), "bplisp"),
    (os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "state_machine.animlang"), "animlang"),
    (os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "third_person_char.animlang"), "animlang"),
    (os.path.join(_REF, "AnimationBP2FP-N", "DSL", "Examples", "simple_blend.animlang"), "animlang"),
]


@pytest.mark.parametrize("path,lang", _REAL)
def test_real_reference_files_zero_errors(path, lang):
    if not os.path.isfile(path):
        pytest.skip(f"reference fixture absent: {path}")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    errors = [i for i in V.dsl_validate(text, lang) if i.severity == "error"]
    assert errors == [], f"{path}: {errors}"
