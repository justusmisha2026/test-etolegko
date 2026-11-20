import json

import httpx
import pytest

from adrobot.errors import AppError
from adrobot.keitaro import KeitaroClient


async def test_pagination_and_group_parameter():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("groups"):
            assert dict(request.url.params) == {"type": "campaigns"}
            return httpx.Response(200, json=[{"id": 1}])
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200, json=[{"id": i + 1} for i in range(offset, min(offset + 100, 103))]
        )

    async with httpx.AsyncClient(
        base_url="https://tracker.example/admin_api/v1/",
        transport=httpx.MockTransport(handler),
    ) as http:
        client = KeitaroClient(http)
        assert len(await client.list_records("campaigns")) == 103
        assert await client.list_records("groups") == [{"id": 1}]
    assert len(calls) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502])
async def test_error_body_never_leaks_and_uncertainty_is_explicit(status):
    async with httpx.AsyncClient(
        base_url="https://tracker.example/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="secret-key")
        ),
    ) as http:
        with pytest.raises(AppError) as caught:
            await KeitaroClient(http).create("campaigns", {})
        assert "secret-key" not in str(caught.value)
        assert caught.value.uncertain == (status >= 500)


async def test_transport_timeout_is_not_retried():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("secret-key", request=request)

    async with httpx.AsyncClient(
        base_url="https://tracker.example/", transport=httpx.MockTransport(handler)
    ) as http:
        with pytest.raises(AppError) as caught:
            await KeitaroClient(http).create("campaigns", {})
        assert caught.value.uncertain
        assert "secret-key" not in str(caught.value)
    assert count == 1


async def test_offer_update_sends_only_offers():
    payload = {"offers": [{"offer_id": 1, "share": 100, "state": "active"}]}

    def handler(request):
        assert request.method == "PUT"
        assert request.url.path == "/admin_api/v1/streams/3"
        assert json.loads(request.content) == payload
        return httpx.Response(200, json={"id": 3, **payload})

    async with httpx.AsyncClient(
        base_url="https://tracker.example/admin_api/v1/",
        transport=httpx.MockTransport(handler),
    ) as http:
        assert (await KeitaroClient(http).update("streams", 3, payload))["id"] == 3


@pytest.mark.parametrize("body", ["not json", "{}", "[]"])
async def test_malformed_write_response_is_uncertain(body):
    async with httpx.AsyncClient(
        base_url="https://tracker.example/",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
    ) as http:
        with pytest.raises(AppError) as caught:
            await KeitaroClient(http).create("campaigns", {})
        assert caught.value.uncertain
