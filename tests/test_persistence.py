import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import inspect

from adrobot.config import Settings
from adrobot.database import Base, OperationRepository, make_engine
from adrobot.main import create_app
from adrobot.schemas import CampaignInput
from adrobot.services import CampaignService


def test_migration_roundtrip_and_operation_survives_restart(
    tmp_path, monkeypatch, gateway, campaign_input
):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("ADROBOT_DATABASE_URL", database_url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    settings = Settings(
        demo=False,
        database_url=database_url,
        api_key=SecretStr("test-key"),
        campaign_prefix="test_example_",
        writes_enabled=True,
        _env_file=None,
    )
    with TestClient(
        create_app(settings, gateway), headers={"X-Adrobot-Request": "1"}
    ) as client:
        first = client.post("/api/campaigns", json=campaign_input).json()
        assert first["status"] == "complete"
    with TestClient(
        create_app(settings, gateway), headers={"X-Adrobot-Request": "1"}
    ) as client:
        second = client.post("/api/campaigns", json=campaign_input).json()
        assert second == first
    assert len(gateway.records["campaigns"]) == 1
    command.downgrade(config, "base")
    engine = make_engine(database_url)
    assert "operations" not in inspect(engine).get_table_names()
    engine.dispose()
    command.upgrade(config, "head")


async def test_concurrent_requests_with_same_key_create_once(
    tmp_path, gateway, campaign_input
):
    engine = make_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")
    Base.metadata.create_all(engine)
    service = CampaignService(
        gateway, OperationRepository(engine), Settings(demo=True, _env_file=None)
    )
    data = CampaignInput.model_validate(campaign_input)
    results = await asyncio.gather(service.create(data), service.create(data))
    assert all(result.status == "complete" for result in results)
    assert len(gateway.records["campaigns"]) == 1
    assert len(gateway.records["streams"]) == 2
    engine.dispose()
