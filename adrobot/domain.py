import hashlib
import json
from typing import Any

from adrobot.errors import AppError
from adrobot.keitaro import Record
from adrobot.schemas import CampaignInput

GOOGLE_URL = "https://google.com/"


def fingerprint(value: Any) -> str:
    serialized = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def campaign_payload(data: CampaignInput, prefix: str) -> Record:
    name = data.name if data.name.startswith(prefix) else f"{prefix}{data.name}"
    return {
        "alias": f"adrobot-{data.operation_id.hex}",
        "name": name,
        "type": "position",
        "state": "disabled",
        "bind_visitors": None,
        "domain_id": data.domain_id,
        "group_id": str(data.group_id),
        "traffic_source_id": data.traffic_source_id,
    }


def stream_payloads(data: CampaignInput, campaign_id: int) -> list[Record]:
    common = {"campaign_id": campaign_id, "state": "active", "collect_clicks": True}
    return [
        {
            **common,
            "name": "Adrobot: Google GEO",
            "type": "forced",
            "position": 1,
            "schema": "redirect",
            "action_type": "http",
            "action_payload": GOOGLE_URL,
            "filter_or": False,
            "filters": [
                {"name": "country", "mode": "accept", "payload": [data.country]}
            ],
        },
        {
            **common,
            "name": "Adrobot: Offers",
            "type": "default",
            "position": 2,
            "schema": "landings",
            "action_type": "http",
            "filters": [],
            "landings": [],
            "offers": [{"offer_id": data.offer_id, "share": 100, "state": "active"}],
        },
    ]


def offer_projection(stream: Record) -> list[Record]:
    return [
        {
            key: offer.get(key, "active" if key == "state" else None)
            for key in ("offer_id", "share", "state")
        }
        for offer in stream.get("offers", [])
    ]


def distribute_offers(offer_ids: list[int], current: list[Record]) -> list[Record]:
    if not offer_ids or len(offer_ids) > 100 or len(set(offer_ids)) != len(offer_ids):
        raise AppError("Нужно от 1 до 100 уникальных офферов", 422)
    states = {row["offer_id"]: row.get("state", "active") for row in current}
    active_ids = [
        offer_id for offer_id in offer_ids if states.get(offer_id, "active") == "active"
    ]
    if not active_ids:
        raise AppError("Нельзя удалить последний активный оффер", 422)
    base, remainder = divmod(100, len(active_ids))
    shares = {
        offer_id: base + (index >= len(active_ids) - remainder)
        for index, offer_id in enumerate(active_ids)
    }
    return [
        {
            "offer_id": offer_id,
            "share": shares.get(offer_id, 0),
            "state": states.get(offer_id, "active"),
        }
        for offer_id in offer_ids
    ]


def contains_values(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and contains_values(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if isinstance(actual, str):
            try:
                actual = json.loads(actual)
            except ValueError:
                return False
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                contains_values(a, b) for a, b in zip(actual, expected, strict=True)
            )
        )
    return actual == expected
