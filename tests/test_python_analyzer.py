import json, pathlib
from backend.app.analyzers.python_analyzer import analyze_python

FIXTURE_DIR   = "tests/fixtures/sample_py"
EXPECTED_FILE = "tests/expected/phase1_python.json"

def _serialize(records) -> str:
    # Match generate_expected.py exactly: default=__dict__, indent=2
    return json.dumps(
        [r.__dict__ for r in records],
        indent=2,
    )

def test_matches_expected_byte_for_byte():
    produced = _serialize(analyze_python(FIXTURE_DIR))
    expected = pathlib.Path(EXPECTED_FILE).read_text(encoding="utf-8")
    assert produced.strip() == expected.strip()

def test_deterministic_across_runs():
    first  = _serialize(analyze_python(FIXTURE_DIR))
    second = _serialize(analyze_python(FIXTURE_DIR))
    assert first == second

def test_no_extra_ir_fields():
    for r in analyze_python(FIXTURE_DIR):
        assert set(r.__dict__.keys()) == {
            "file_path", "name", "type", "start_line", "end_line"
        }

def test_async_function_present():
    types = {r.type for r in analyze_python(FIXTURE_DIR)}
    assert "async_function" in types

def test_parse_error_record_present():
    recs = analyze_python(FIXTURE_DIR)
    errs = [r for r in recs if r.type == "_parse_error"]
    assert len(errs) == 1
    assert errs[0].file_path.endswith("bad_syntax.py")

def test_posix_paths_only():
    for r in analyze_python(FIXTURE_DIR):
        assert "\\" not in r.file_path
