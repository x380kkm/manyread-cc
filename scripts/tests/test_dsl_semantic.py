"""Tests for the OPTIONAL SEMANTIC (schema / type-dictionary) layer of the
pre-flight DSL validator (scripts/dsl_validate.py).

The structural layer is covered by test_dsl_validate.py (those 20 tests call
dsl_validate(text, lang) with NO schema and must stay byte-identical — re-run
them too). This suite covers ONLY the schema-driven semantic pass added on top:

  * no-schema path is byte-identical to the structural-only result;
  * the bundled matlang.sample.json makes the two REAL examples 0-error;
  * crafted bad files surface MISSING_REQUIRED_PIN / UNKNOWN_PROP /
    UNKNOWN_NODE_TYPE with the right code + severity;
  * the semantic result is deterministic across runs;
  * a malformed schema raises a clean ValueError (no crash) and the CLI exits 2.

Run from scripts/ WITH the tree-sitter deps, e.g.:
    cd scripts && uv run --python 3.12 --with pytest --with "tree-sitter>=0.23" \
        --with tree-sitter-language-pack -m pytest tests/test_dsl_semantic.py -q
"""
import json
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

_SCHEMA_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "schemas", "matlang.sample.json"))


def _schema():
    return V.load_schema(_SCHEMA_PATH)


def _codes(text, lang, schema=None, sev=None):
    return sorted(i.code for i in V.dsl_validate(text, lang, schema)
                  if sev is None or i.severity == sev)


# --- (a) no-schema path is byte-identical to structural-only -----------------
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


def test_no_schema_byte_identical_to_two_arg():
    # The 3rd positional defaults to None; the 2-arg and 3-arg(None) results must
    # be identical objects (the existing 20 tests rely on this).
    two_arg = V.dsl_validate(_GOOD_MATLANG, "matlang")
    three_none = V.dsl_validate(_GOOD_MATLANG, "matlang", None)
    assert two_arg == three_none


def test_schema_adds_no_errors_to_good_inline():
    # adding the sample schema must not introduce any ERROR on a valid file.
    assert _codes(_GOOD_MATLANG, "matlang", _schema(), "error") == []


# --- (b) every real example validates 0 SEMANTIC errors against the sample ----
_REF = r"W:\cc\reference"
_REAL_MATLANG = [
    os.path.join(_REF, "MaterialBP2DSL", "DSL", "Examples", "simple_pbr.matlang"),
    os.path.join(_REF, "MaterialBP2DSL", "DSL", "Examples", "emissive_rim.matlang"),
]


@pytest.mark.parametrize("path", _REAL_MATLANG)
def test_real_examples_zero_semantic_errors(path):
    if not os.path.isfile(path):
        pytest.skip(f"reference fixture absent: {path}")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    issues = V.dsl_validate(text, "matlang", _schema())
    # the sample schema is INFERRED from these files -> ZERO semantic errors and
    # ZERO semantic warnings (every observed type + prop is in the dictionary).
    semantic_codes = {"UNKNOWN_NODE_TYPE", "UNKNOWN_PROP", "MISSING_REQUIRED_PIN"}
    sem = [i for i in issues if i.code in semantic_codes]
    assert sem == [], f"{path}: unexpected semantic issues {sem}"
    assert [i for i in issues if i.severity == "error"] == []


# --- (c) crafted bad files: each semantic code with the right severity --------
def test_missing_required_pin_is_error():
    # component-mask with NO :input -> the one genuinely-required pin (no const
    # fallback) is unconnected -> MISSING_REQUIRED_PIN error.
    bad = ('(material "M" (expressions'
           ' (component-mask $cm1 :mask "rg"))'
           " (outputs :base-color (connect $cm1 0)))")
    issues = V.dsl_validate(bad, "matlang", _schema())
    miss = [i for i in issues if i.code == "MISSING_REQUIRED_PIN"]
    assert len(miss) == 1
    assert miss[0].severity == "error"
    assert ":input" in miss[0].message
    # and with NO schema the same file has no MISSING_REQUIRED_PIN (structural-only).
    assert "MISSING_REQUIRED_PIN" not in _codes(bad, "matlang")


def test_required_pin_connected_is_clean():
    # component-mask WITH its :input connected -> no MISSING_REQUIRED_PIN.
    ok = ('(material "M" (expressions'
          " (constant3-vector $v1 :value (1.0 0.0 0.0))"
          ' (component-mask $cm1 :mask "rg" :input (connect $v1 0)))'
          " (outputs :base-color (connect $cm1 0)))")
    issues = V.dsl_validate(ok, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}


def test_multiply_optional_pin_unconnected_is_not_error():
    # REGRESSION GUARD for the false-positive the audit flagged: multiply.{a,b}
    # are required:false (UE ConstA/ConstB fallbacks). A real round-tripped material
    # that leaves :b on its const default exports `(multiply $m :a (connect ...))`
    # with NO :b wire -> this is VALID and must NOT raise MISSING_REQUIRED_PIN.
    ok = ('(material "M" (expressions'
          " (constant $c1 :value 1.0)"
          " (multiply $m1 :a (connect $c1 0)))"
          " (outputs :base-color (connect $m1 0)))")
    issues = V.dsl_validate(ok, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}
    assert [i for i in issues if i.severity == "error"] == []
    # even a multiply with NEITHER :a nor :b is not a semantic error (both optional).
    ok2 = ('(material "M" (expressions (multiply $m1))'
           " (outputs :base-color (connect $m1 0)))")
    issues2 = V.dsl_validate(ok2, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues2}


def test_unknown_prop_is_warning():
    # :clamp-result is not a known property OR pin of multiply -> warning, not error.
    bad = ('(material "M" (expressions'
           " (constant $c1 :value 1.0) (constant $c2 :value 2.0)"
           " (multiply $m1 :a (connect $c1 0) :b (connect $c2 0) :clamp-result true))"
           " (outputs :base-color (connect $m1 0)))")
    issues = V.dsl_validate(bad, "matlang", _schema())
    unk = [i for i in issues if i.code == "UNKNOWN_PROP"]
    assert len(unk) == 1
    assert unk[0].severity == "warning"
    assert ":clamp-result" in unk[0].message
    # required pins ARE connected -> no MISSING_REQUIRED_PIN here.
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}


def test_unknown_node_type_is_warning():
    # 'panner' is a real UE type but NOT in the PARTIAL sample schema -> warning.
    bad = ('(material "M" (expressions'
           " (panner $p1 :speed-x 0.1)"
           " (constant $c1 :value 1.0))"
           " (outputs :base-color (connect $c1 0)))")
    issues = V.dsl_validate(bad, "matlang", _schema())
    unk = [i for i in issues if i.code == "UNKNOWN_NODE_TYPE"]
    assert len(unk) == 1
    assert unk[0].severity == "warning"
    assert "panner" in unk[0].message
    # an unknown type is NEVER an error in a partial dictionary.
    assert all(i.severity != "error" for i in issues if i.code == "UNKNOWN_NODE_TYPE")


def test_optional_pin_unconnected_is_not_flagged():
    # texture-sample.uv is required:false -> leaving it unconnected is fine
    # (UE falls back to default mesh UVs). No MISSING_REQUIRED_PIN.
    ok = ('(material "M" (expressions'
          ' (texture-sample $t1 :texture (asset "/Game/T")))'
          " (outputs :base-color (connect $t1 0)))")
    issues = V.dsl_validate(ok, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}


def test_absent_optional_property_not_flagged():
    # fresnel with NO :exponent / :base-reflect-fraction -> absent == CDO default,
    # NEVER an error or warning (delta-serialization invariant).
    ok = ('(material "M" (expressions'
          " (fresnel $f1)"
          " (constant $c1 :value 1.0))"
          " (outputs :base-color (connect $c1 0)))")
    issues = V.dsl_validate(ok, "matlang", _schema())
    sem = [i for i in issues
           if i.code in ("UNKNOWN_PROP", "MISSING_REQUIRED_PIN")]
    assert sem == []


def test_material_root_props_not_flagged():
    # the 'material' ROOT is validated via its own schema entry; its header
    # keywords (:domain etc.) are known props, NOT UNKNOWN_PROP, and the outputs
    # block slot keywords (:base-color etc.) live on a non-node row that is SKIPPED.
    issues = V.dsl_validate(_GOOD_MATLANG, "matlang", _schema())
    assert "UNKNOWN_PROP" not in {i.code for i in issues}
    assert "UNKNOWN_NODE_TYPE" not in {i.code for i in issues}


def test_value_less_keyword_does_not_misclassify_next_pin():
    # DEFENSIVE-PAIRING guard: a malformed value-less keyword (:mask with no value,
    # immediately followed by :input) must NOT swallow :input as its value. :input
    # stays a recognized (connected) pin -> no spurious MISSING_REQUIRED_PIN, and
    # :mask is recorded as a (known) property.
    src = ('(material "M" (expressions'
           " (constant3-vector $v1 :value (1.0 0.0 0.0))"
           " (component-mask $cm1 :mask :input (connect $v1 0)))"
           " (outputs :base-color (connect $cm1 0)))")
    issues = V.dsl_validate(src, "matlang", _schema())
    assert "MISSING_REQUIRED_PIN" not in {i.code for i in issues}


# --- (d) determinism ----------------------------------------------------------
def test_semantic_deterministic():
    bad = ('(material "M" (expressions'
           " (multiply $m1 :extra1 1 :extra2 2)"
           " (panner $p1)"
           " (constant $c1 :value 1.0))"
           " (outputs :base-color (connect $c1 0)))")
    sch = _schema()
    a = V.dsl_validate(bad, "matlang", sch)
    b = V.dsl_validate(bad, "matlang", sch)
    assert a == b
    assert a == sorted(a, key=lambda i: i.sort_key())


# --- (e) malformed schema -> clean error, not a crash -------------------------
def test_load_schema_good():
    sch = _schema()
    assert "matlang" in sch
    # multiply.{a,b} are OPTIONAL (UE ConstA/ConstB fallbacks); component-mask.input
    # is the one genuinely-required pin (no const fallback).
    assert sch["matlang"]["multiply"]["pins"]["a"]["required"] is False
    assert sch["matlang"]["multiply"]["pins"]["b"]["required"] is False
    assert sch["matlang"]["component-mask"]["pins"]["input"]["required"] is True


def test_load_schema_root_not_object(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        V.load_schema(str(p))


def test_load_schema_bad_lang_value(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"matlang": 42}', encoding="utf-8")
    with pytest.raises(ValueError):
        V.load_schema(str(p))


def test_load_schema_bad_pins_shape(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"matlang": {"multiply": {"pins": {"a": {"required": "yes"}}}}}',
                 encoding="utf-8")
    with pytest.raises(ValueError):
        V.load_schema(str(p))


def test_load_schema_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        V.load_schema(str(p))


def test_load_schema_allows_metadata_keys(tmp_path):
    # top-level '$'-prefixed keys (the SAMPLE note) must NOT trip shape validation.
    p = tmp_path / "ok.json"
    p.write_text('{"$schema_note": "hi", "matlang": {"multiply": {"pins": {}}}}',
                 encoding="utf-8")
    sch = V.load_schema(str(p))
    assert "matlang" in sch


def test_cli_malformed_schema_exits_2(tmp_path, capsys):
    # the CLI prints a clean error and returns exit 2, not a traceback.
    src = tmp_path / "m.matlang"
    src.write_text('(material "M" (expressions (constant $c :value 1.0))'
                   " (outputs :base-color (connect $c 0)))", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc = V.main([str(src), "--schema", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "malformed schema" in err


def test_cli_with_schema_runs_semantic(tmp_path):
    # end-to-end: a good file + the sample schema -> exit 0.
    src = tmp_path / "m.matlang"
    src.write_text(_GOOD_MATLANG, encoding="utf-8")
    rc = V.main([str(src), "--schema", _SCHEMA_PATH])
    assert rc == 0
