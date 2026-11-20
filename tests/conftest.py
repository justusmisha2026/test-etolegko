from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.demo import DemoKeitaro
from adrobot.main import create_app


@pytest.fixture
def gateway():
    return DemoKeitaro()


@pytest.fixture
def client(gateway):
    app = create_app(Settings(demo=True, _env_file=None), gateway)
    with TestClient(app, headers={"X-Adrobot-Request": "1"}) as client:
        yield client


@pytest.fixture
def campaign_input():
    return {
        "operation_id": str(uuid4()),
        "name": "Australia",
        "country": "AU",
        "offer_id": 1,
        "domain_id": 1,
        "group_id": 1,
        "traffic_source_id": 1,
    }
