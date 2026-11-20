from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.main import create_app


def test_static_ui_and_configuration(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/status").json()["demo"]
    assert client.get("/api/countries").status_code == 200


def test_cross_origin_mutation_is_blocked(client, campaign_input):
    response = client.post(
        "/api/campaigns",
        json=campaign_input,
        headers={"Origin": "https://unrelated.example"},
    )
    assert response.status_code == 403
    assert client.get("/api/operations").json() == []


def test_missing_custom_header_is_blocked(campaign_input):
    with TestClient(create_app(Settings(demo=True, _env_file=None))) as client:
        assert client.post("/api/campaigns", json=campaign_input).status_code == 403


def test_untrusted_host_is_blocked(client):
    assert (
        client.get("/api/catalogs", headers={"Host": "attacker.example"}).status_code
        == 400
    )
