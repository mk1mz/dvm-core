import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))


def load(name):
    return json.loads((ROOT / "examples" / name).read_text())


@pytest.fixture
def psl_doc():
    return load("portrait-square-landscape.json")


@pytest.fixture
def mutate(psl_doc):
    """Return a deep copy of the main example with a mutation applied."""
    def _m(fn):
        d = copy.deepcopy(psl_doc)
        fn(d)
        return d
    return _m
