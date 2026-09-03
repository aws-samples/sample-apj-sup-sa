import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.render_dashboard import render, CfnLoader  # noqa: E402

TEMPLATE_PATH = ROOT / "cloudformation" / "dashboard.yaml"


@pytest.fixture(scope="session")
def template():
    with open(TEMPLATE_PATH) as fh:
        return yaml.load(fh, Loader=CfnLoader)


@pytest.fixture(scope="session")
def body_tier1():
    return json.loads(render(1, {}))


@pytest.fixture(scope="session")
def body_tier2():
    return json.loads(render(2, {}))


@pytest.fixture(scope="session")
def body_tier3():
    return json.loads(render(3, {}))
