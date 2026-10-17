import json
import subprocess
import sys

import pytest

from conftest import EXAMPLES, ROOT

jsonschema = pytest.importorskip("jsonschema")
SCHEMA = json.loads((ROOT / "schemas" / "dvm-timeline-1.schema.json").read_text())


@pytest.fixture
def validator():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    return jsonschema.Draft202012Validator(SCHEMA)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_pass_schema(validator, path):
    validator.validate(json.loads(path.read_text()))


@pytest.mark.parametrize("fn", [
    lambda d: d.update(version=2),
    lambda d: d.pop("canvas"),
    lambda d: d.update(extra=1),
    lambda d: d["events"][0]["viewport"].update(width=0),
    lambda d: d["events"][0]["viewport"].update(x=-1),
    lambda d: d["events"][1].update(transition="ease"),
    lambda d: d["events"][1].update(duration=0),
    lambda d: d["events"][1].update(start="abc"),
    lambda d: d.update(events=[]),
])
def test_schema_rejects_structural_errors(validator, mutate, fn):
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(mutate(fn))


def run(*args):
    return subprocess.run([sys.executable, "-m", "dvm", *args], capture_output=True, text=True,
                          env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""})


def test_cli_validate_ok():
    r = run("validate", str(ROOT / "examples" / "portrait-square-landscape.json"))
    assert r.returncode == 0 and "4 events" in r.stdout


def test_cli_validate_reports_readable_errors(tmp_path, psl_doc):
    psl_doc["events"][1]["viewport"]["width"] = -3
    psl_doc["events"][2]["start"] = 7
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(psl_doc))
    r = run("validate", str(f))
    assert r.returncode == 1
    assert "events[1].viewport.width" in r.stderr and "gap after previous event" in r.stderr


def test_cli_missing_and_malformed_files(tmp_path):
    assert run("validate", str(tmp_path / "nope.json")).returncode == 2
    f = tmp_path / "x.json"
    f.write_text("{not json")
    assert run("validate", str(f)).returncode == 2


def test_cli_at_and_compile():
    ex = str(ROOT / "examples" / "portrait-square-landscape.json")
    r = run("at", ex, "3.25")
    assert json.loads(r.stdout) == {"x": 210, "y": 0, "width": 1500, "height": 1920}
    r = run("compile", ex, "--fps", "30", "--duration", "12")
    out = json.loads(r.stdout)
    assert out["frames"] == 360 and out["samples"] == 46 and out["payload_bytes"] == 552
