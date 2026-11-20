from copy import deepcopy
from uuid import uuid4

import pytest

from adrobot.domain import GOOGLE_URL
from adrobot.errors import AppError


def test_create_and_repeated_request(client, gateway, campaign_input):
    response = client.post("/api/campaigns", json=campaign_input)
    assert response.status_code == 201
    operation = response.json()
    assert operation["status"] == "complete"
    assert (
        client.post("/api/campaigns", json=campaign_input).json()["id"]
        == operation["id"]
    )
    assert len(gateway.records["campaigns"]) == 1
    campaign = gateway.records["campaigns"][0]
    assert campaign["state"] == "active"
    assert campaign["name"] == "test_demo_Australia"
    assert campaign["bind_visitors"] is None
    google, offers = gateway.records["streams"]
    assert google["type"] == "forced"
    assert google["filters"] == [
        {"name": "country", "mode": "accept", "payload": ["AU"]}
    ]
    assert google["action_payload"] == GOOGLE_URL
    assert offers["type"] == "default"
    assert offers["offers"] == [{"offer_id": 1, "share": 100, "state": "active"}]


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "  "),
        ("country", "ZZ"),
        ("offer_id", True),
        ("offer_id", "1"),
        ("domain_id", -1),
    ],
)
def test_invalid_input_does_not_write(client, gateway, campaign_input, field, value):
    campaign_input[field] = value
    assert client.post("/api/campaigns", json=campaign_input).status_code == 422
    assert not gateway.records["campaigns"]


def test_missing_reference_does_not_write(client, gateway, campaign_input):
    campaign_input["offer_id"] = 999
    result = client.post("/api/campaigns", json=campaign_input).json()
    assert result["status"] == "failed"
    assert result["step"] == "validation"
    assert not gateway.records["campaigns"]


def test_reused_key_with_different_payload_conflicts(client, gateway, campaign_input):
    client.post("/api/campaigns", json=campaign_input)
    campaign_input["country"] = "US"
    assert client.post("/api/campaigns", json=campaign_input).status_code == 409
    assert len(gateway.records["campaigns"]) == 1


@pytest.mark.parametrize("failure_step", [1, 2, 3, 4])
def test_failure_at_each_write_is_journaled(
    client, gateway, campaign_input, monkeypatch, failure_step
):
    calls = 0
    create = gateway.create
    update = gateway.update

    async def failing_create(resource, payload):
        nonlocal calls
        calls += 1
        if calls == failure_step:
            raise AppError("Запись отклонена", 502)
        return await create(resource, payload)

    async def failing_update(resource, record_id, payload):
        nonlocal calls
        calls += 1
        if calls == failure_step:
            raise AppError("Запись отклонена", 502)
        return await update(resource, record_id, payload)

    monkeypatch.setattr(gateway, "create", failing_create)
    monkeypatch.setattr(gateway, "update", failing_update)
    result = client.post("/api/campaigns", json=campaign_input).json()
    assert result["status"] == ("failed" if failure_step == 1 else "partial")
    if gateway.records["campaigns"]:
        assert gateway.records["campaigns"][0]["state"] == "disabled"
    client.post("/api/campaigns", json=campaign_input)
    assert calls == failure_step


def test_timeout_after_campaign_creation_can_be_located_without_replay(
    client, gateway, campaign_input, monkeypatch
):
    original = gateway.create

    async def timeout(resource, payload):
        await original(resource, payload)
        raise AppError("Тайм-аут", 502, uncertain=True)

    monkeypatch.setattr(gateway, "create", timeout)
    result = client.post("/api/campaigns", json=campaign_input).json()
    assert result["status"] == "uncertain"
    assert result["campaign_id"] is None
    recovered = client.post(f"/api/operations/{result['id']}/reconcile").json()
    assert recovered["campaign_id"] == gateway.records["campaigns"][0]["id"]
    assert recovered["status"] != "complete"
    assert len(gateway.records["campaigns"]) == 1
    assert len(gateway.records["streams"]) == 0


def test_timeout_after_activation_reconciles_to_complete(
    client, gateway, campaign_input, monkeypatch
):
    original = gateway.update

    async def timeout(resource, record_id, payload):
        await original(resource, record_id, payload)
        raise AppError("Тайм-аут", 502, uncertain=True)

    monkeypatch.setattr(gateway, "update", timeout)
    result = client.post("/api/campaigns", json=campaign_input).json()
    assert result["status"] == "uncertain"
    assert (
        client.post(f"/api/operations/{result['id']}/reconcile").json()["status"]
        == "complete"
    )
    assert len(gateway.records["streams"]) == 2


def test_incomplete_configuration_is_not_activated(
    client, gateway, campaign_input, monkeypatch
):
    original = gateway.create

    async def omit_filter(resource, payload):
        modified = deepcopy(payload)
        if modified.get("type") == "forced":
            modified["filters"] = []
        return await original(resource, modified)

    monkeypatch.setattr(gateway, "create", omit_filter)
    assert (
        client.post("/api/campaigns", json=campaign_input).json()["status"] == "partial"
    )
    assert gateway.records["campaigns"][0]["state"] == "disabled"


def test_editor_only_changes_target_offers(client, gateway, campaign_input):
    first = client.post("/api/campaigns", json=campaign_input).json()["campaign_id"]
    campaign_input["operation_id"] = str(uuid4())
    client.post("/api/campaigns", json=campaign_input)
    target = gateway.records["streams"][1]
    target["comments"] = "Preserve me"
    before = deepcopy(gateway.records)
    editor = client.get(f"/api/campaigns/{first}").json()
    response = client.put(
        f"/api/campaigns/{first}/streams/{target['id']}/offers",
        json={"revision": editor["revision"], "offer_ids": [1, 2, 3]},
    )
    assert response.status_code == 200
    expected = [
        {"offer_id": 1, "share": 33, "state": "active"},
        {"offer_id": 2, "share": 33, "state": "active"},
        {"offer_id": 3, "share": 34, "state": "active"},
    ]
    assert target["offers"] == expected
    before["streams"][1]["offers"] = expected
    assert gateway.records == before
    response = client.put(
        f"/api/campaigns/{first}/streams/{target['id']}/offers",
        json={"revision": response.json()["revision"], "offer_ids": [2, 3]},
    )
    assert response.status_code == 200
    assert [offer["share"] for offer in target["offers"]] == [50, 50]


def test_stale_form_does_not_overwrite(client, gateway, campaign_input):
    campaign_id = client.post("/api/campaigns", json=campaign_input).json()[
        "campaign_id"
    ]
    editor = client.get(f"/api/campaigns/{campaign_id}").json()
    stream = gateway.records["streams"][1]
    stream["comments"] = "Changed outside the app"
    response = client.put(
        f"/api/campaigns/{campaign_id}/streams/{stream['id']}/offers",
        json={"revision": editor["revision"], "offer_ids": [1, 2]},
    )
    assert response.status_code == 409
    assert len(stream["offers"]) == 1


@pytest.mark.parametrize("ids", [[], [1, 1], [999]])
def test_editor_rejects_invalid_offer_set(client, gateway, campaign_input, ids):
    campaign_id = client.post("/api/campaigns", json=campaign_input).json()[
        "campaign_id"
    ]
    editor = client.get(f"/api/campaigns/{campaign_id}").json()
    stream = gateway.records["streams"][1]
    response = client.put(
        f"/api/campaigns/{campaign_id}/streams/{stream['id']}/offers",
        json={"revision": editor["revision"], "offer_ids": ids},
    )
    assert response.status_code == 422
    assert len(stream["offers"]) == 1


def test_other_prefix_is_read_only(client, gateway, campaign_input):
    campaign_id = client.post("/api/campaigns", json=campaign_input).json()[
        "campaign_id"
    ]
    gateway.records["campaigns"][0]["name"] = "another_owner"
    editor = client.get(f"/api/campaigns/{campaign_id}").json()
    stream_id = gateway.records["streams"][1]["id"]
    assert not editor["editable"]
    assert (
        client.put(
            f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers",
            json={"revision": editor["revision"], "offer_ids": [1, 2]},
        ).status_code
        == 403
    )


def test_empty_binding_response_is_disabled_binding(
    client, gateway, campaign_input, monkeypatch
):
    original = gateway.create

    async def normalize(resource, payload):
        result = await original(resource, payload)
        if resource == "campaigns":
            gateway.records["campaigns"][-1]["bind_visitors"] = ""
        return result

    monkeypatch.setattr(gateway, "create", normalize)
    assert (
        client.post("/api/campaigns", json=campaign_input).json()["status"]
        == "complete"
    )


def test_resume_partial_creation_without_duplicate_stream(
    client, gateway, campaign_input, monkeypatch
):
    original = gateway.create

    async def reject_offer_stream(resource, payload):
        if payload.get("type") == "default":
            raise AppError("Отклонено", 502)
        return await original(resource, payload)

    monkeypatch.setattr(gateway, "create", reject_offer_stream)
    result = client.post("/api/campaigns", json=campaign_input).json()
    assert result["status"] == "partial"
    monkeypatch.setattr(gateway, "create", original)
    resumed = client.post(f"/api/operations/{result['id']}/resume")
    assert resumed.json()["status"] == "complete"
    assert len(gateway.records["campaigns"]) == 1
    assert len(gateway.records["streams"]) == 2


def test_resume_does_not_replay_uncertain_write(
    client, gateway, campaign_input, monkeypatch
):
    async def timeout(resource, payload):
        raise AppError("Неизвестно", 502, uncertain=True)

    monkeypatch.setattr(gateway, "create", timeout)
    result = client.post("/api/campaigns", json=campaign_input).json()
    assert client.post(f"/api/operations/{result['id']}/resume").status_code == 409
