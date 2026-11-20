import asyncio
import logging
from typing import Any

from adrobot.config import Settings
from adrobot.database import Operation, OperationRepository
from adrobot.domain import (
    campaign_payload,
    contains_values,
    distribute_offers,
    fingerprint,
    offer_projection,
    stream_payloads,
)
from adrobot.errors import AppError
from adrobot.keitaro import KeitaroGateway, Record
from adrobot.schemas import CampaignInput, OffersInput

CATALOGS = ("domains", "groups", "traffic_sources", "offers")
logger = logging.getLogger(__name__)


class CampaignService:
    def __init__(
        self,
        gateway: KeitaroGateway,
        repository: OperationRepository,
        settings: Settings,
    ) -> None:
        self.gateway = gateway
        self.repository = repository
        self.settings = settings
        self.write_lock = asyncio.Lock()

    @property
    def prefix(self) -> str:
        return "test_demo_" if self.settings.demo else self.settings.campaign_prefix

    def require_connection(self) -> None:
        if message := self.settings.configuration_error():
            raise AppError(message, 503)

    def require_writes(self) -> None:
        self.require_connection()
        if not self.settings.demo and not self.settings.writes_enabled:
            raise AppError("Запись отключена до проверки контракта стенда", 503)
        if message := self.settings.creation_error():
            raise AppError(message, 503)

    async def catalogs(self) -> dict[str, list[Record]]:
        self.require_connection()
        result = {}
        for resource in CATALOGS:
            result[resource] = [
                {"id": row["id"], "name": row.get("name", str(row["id"]))}
                for row in await self.gateway.list_records(resource)
                if row.get("state") not in ("deleted", "disabled")
            ]
        return result

    async def campaigns(self) -> list[Record]:
        self.require_connection()
        return [
            {
                "id": row["id"],
                "name": row.get("name", str(row["id"])),
                "state": row.get("state"),
                "editable": bool(
                    self.prefix and row.get("name", "").startswith(self.prefix)
                ),
            }
            for row in await self.gateway.list_records("campaigns")
            if row.get("state") != "deleted"
        ]

    async def snapshot(self, campaign_id: int) -> Record:
        self.require_connection()
        campaign = await self.gateway.get_record("campaigns", campaign_id)
        streams = await self.gateway.streams(campaign_id)
        return {
            "campaign": campaign,
            "streams": sorted(streams, key=lambda row: row["id"]),
        }

    async def editor(self, campaign_id: int) -> Record:
        snapshot = await self.snapshot(campaign_id)
        campaign = snapshot["campaign"]
        return {
            "id": campaign_id,
            "name": campaign.get("name", str(campaign_id)),
            "revision": fingerprint(snapshot),
            "editable": bool(
                self.prefix and campaign.get("name", "").startswith(self.prefix)
            ),
            "streams": [
                {
                    "id": stream["id"],
                    "name": stream.get("name", str(stream["id"])),
                    "type": stream.get("type"),
                    "schema": stream.get("schema"),
                    "offers": offer_projection(stream),
                }
                for stream in snapshot["streams"]
            ],
        }

    async def _validate_references(self, data: CampaignInput) -> None:
        catalogs = await self.catalogs()
        for resource, value in (
            ("domains", data.domain_id),
            ("groups", data.group_id),
            ("traffic_sources", data.traffic_source_id),
            ("offers", data.offer_id),
        ):
            if value not in {row["id"] for row in catalogs[resource]}:
                raise AppError(
                    f"Выбранная запись {resource} отсутствует или недоступна", 422
                )

    async def update_offers(
        self, campaign_id: int, stream_id: int, data: OffersInput
    ) -> Record:
        self.require_writes()
        if not self.settings.editor_enabled:
            raise AppError("Редактор отключён конфигурацией", 403)
        async with self.write_lock:
            before = await self.snapshot(campaign_id)
            campaign = before["campaign"]
            if not campaign.get("name", "").startswith(self.prefix):
                raise AppError(
                    "Редактировать можно только кампании со своим префиксом", 403
                )
            if fingerprint(before) != data.revision:
                raise AppError("Кампания изменилась. Загрузите актуальные данные", 409)
            stream = next(
                (row for row in before["streams"] if row["id"] == stream_id), None
            )
            if stream is None:
                raise AppError("Поток не принадлежит выбранной кампании", 404)
            if stream.get("schema") != "landings" or not stream.get("offers"):
                raise AppError("Этот поток не поддерживает редактирование офферов", 422)
            current = offer_projection(stream)
            if [row["offer_id"] for row in current] == data.offer_ids:
                return await self.editor(campaign_id)
            available = {row["id"] for row in (await self.catalogs())["offers"]}
            existing = {row["offer_id"] for row in current}
            if any(offer_id not in available | existing for offer_id in data.offer_ids):
                raise AppError("Добавляемый оффер отсутствует или недоступен", 422)
            offers = distribute_offers(data.offer_ids, current)
            await self.gateway.update("streams", stream_id, {"offers": offers})
            after = await self.snapshot(campaign_id)
            actual = next(
                (row for row in after["streams"] if row["id"] == stream_id), None
            )
            if actual is None or offer_projection(actual) != offers:
                raise AppError(
                    "Состав или доли после записи не подтверждены. Обновите форму", 409
                )
            before_other = self._without_target_offers(before, stream_id)
            after_other = self._without_target_offers(after, stream_id)
            if before_other != after_other:
                raise AppError(
                    "Обнаружено изменение соседних настроек. Проверьте кампанию", 409
                )
            return await self.editor(campaign_id)

    @staticmethod
    def _without_target_offers(snapshot: Record, stream_id: int) -> Record:
        excluded = {"updated_at"}
        return {
            "campaign": {
                key: value
                for key, value in snapshot["campaign"].items()
                if key not in excluded
            },
            "streams": [
                {
                    key: value
                    for key, value in row.items()
                    if key not in excluded
                    and not (row["id"] == stream_id and key == "offers")
                }
                for row in snapshot["streams"]
            ],
        }

    def _step(self, operation_id: str, step: str, **values: Any) -> None:
        self.repository.update(operation_id, step=step, **values)
        logger.info("operation=%s step=%s", operation_id, step)

    async def create(self, data: CampaignInput) -> Operation:
        self.require_writes()
        request = {"input": data.model_dump(mode="json"), "prefix": self.prefix}
        operation_id = str(data.operation_id)
        async with self.write_lock:
            operation, claimed = self.repository.claim(
                operation_id, fingerprint(request), request
            )
            if not claimed:
                return operation
            try:
                await self._validate_references(data)
                self._step(operation_id, "create_campaign")
                campaign = await self.gateway.create(
                    "campaigns", campaign_payload(data, self.prefix)
                )
                campaign_id = campaign["id"]
                self._step(
                    operation_id, "create_google_stream", campaign_id=campaign_id
                )
                google, offers = stream_payloads(data, campaign_id)
                await self.gateway.create("streams", google)
                self._step(operation_id, "create_offer_stream")
                await self.gateway.create("streams", offers)
                return await self._activate(data, campaign_id)
            except AppError as exc:
                operation = self.repository.get(operation_id)
                status = (
                    "uncertain"
                    if exc.uncertain
                    else ("partial" if operation.campaign_id else "failed")
                )
                return self.repository.update(
                    operation_id, status=status, error=exc.message
                )

    async def _activate(self, data: CampaignInput, campaign_id: int) -> Operation:
        operation_id = str(data.operation_id)
        self._step(operation_id, "verify_disabled")
        await self.verify(data, campaign_id, state="disabled")
        self._step(operation_id, "activate")
        await self.gateway.update("campaigns", campaign_id, {"state": "active"})
        self._step(operation_id, "verify_active")
        await self.verify(data, campaign_id, state="active")
        return self.repository.update(
            operation_id, status="complete", step="done", error=None
        )

    async def resume(self, operation_id: str) -> Operation:
        self.require_writes()
        async with self.write_lock:
            operation = self.repository.get(operation_id)
            if operation.status != "partial" or operation.campaign_id is None:
                raise AppError(
                    "Продолжить можно только подтверждённую частичную операцию", 409
                )
            if operation.request["prefix"] != self.prefix:
                raise AppError(
                    "Префикс операции отличается от текущей конфигурации", 409
                )
            data = CampaignInput.model_validate(operation.request["input"])
            campaign_id = operation.campaign_id
            snapshot = await self.snapshot(campaign_id)
            expected = campaign_payload(data, self.prefix)
            actual = self._normalized_campaign(snapshot["campaign"])
            if not contains_values(actual, expected):
                raise AppError(
                    "Кампания изменена или активна. Сначала выполните сверку", 409
                )
            streams = sorted(
                snapshot["streams"], key=lambda row: row.get("position", 0)
            )
            planned = stream_payloads(data, campaign_id)
            if len(streams) > 2 or not contains_values(
                streams, planned[: len(streams)]
            ):
                raise AppError("Структура частичной кампании изменилась", 409)
            next_steps = ("create_google_stream", "create_offer_stream")
            if len(streams) < 2 and operation.step != next_steps[len(streams)]:
                raise AppError(
                    "Отсутствующий поток не совпадает с незавершённым шагом", 409
                )
            await self._validate_references(data)
            self.repository.update(operation_id, status="running", error=None)
            try:
                for index in range(len(streams), len(planned)):
                    self._step(operation_id, next_steps[index])
                    await self.gateway.create("streams", planned[index])
                return await self._activate(data, campaign_id)
            except AppError as exc:
                return self.repository.update(
                    operation_id,
                    status="uncertain" if exc.uncertain else "partial",
                    error=exc.message,
                )

    @staticmethod
    def _normalized_campaign(campaign: Record) -> Record:
        normalized = dict(campaign)
        normalized["group_id"] = str(normalized.get("group_id"))
        if normalized.get("bind_visitors") == "":
            normalized["bind_visitors"] = None
        return normalized

    async def verify(
        self,
        data: CampaignInput,
        campaign_id: int,
        *,
        state: str,
        prefix: str | None = None,
    ) -> None:
        snapshot = await self.snapshot(campaign_id)
        expected = campaign_payload(data, self.prefix if prefix is None else prefix)
        expected["state"] = state
        actual = self._normalized_campaign(snapshot["campaign"])
        if not contains_values(actual, expected):
            raise AppError("Настройки кампании после записи не совпали с запросом", 409)
        streams = sorted(snapshot["streams"], key=lambda row: row.get("position", 0))
        if not contains_values(streams, stream_payloads(data, campaign_id)):
            raise AppError(
                "Не подтверждены два потока, геофильтр или выбранный оффер", 409
            )

    async def reconcile(self, operation_id: str) -> Operation:
        self.require_connection()
        async with self.write_lock:
            operation = self.repository.get(operation_id)
            if operation.status == "complete":
                return operation
            data = CampaignInput.model_validate(operation.request["input"])
            prefix = operation.request["prefix"]
            campaign_id = operation.campaign_id
            if campaign_id is None:
                alias = campaign_payload(data, prefix)["alias"]
                matches = [
                    row
                    for row in await self.gateway.list_records("campaigns")
                    if row.get("alias") == alias
                ]
                if len(matches) != 1:
                    return self.repository.update(
                        operation_id,
                        error="Однозначный результат не найден. "
                        "Повторная запись не выполнена.",
                    )
                campaign_id = matches[0]["id"]
                self.repository.update(operation_id, campaign_id=campaign_id)
            try:
                await self.verify(data, campaign_id, state="active", prefix=prefix)
            except AppError as exc:
                return self.repository.update(operation_id, error=exc.message)
            return self.repository.update(
                operation_id, status="complete", step="done", error=None
            )


def operation_view(operation: Operation) -> Record:
    return {
        "id": operation.id,
        "status": operation.status,
        "step": operation.step,
        "campaign_id": operation.campaign_id,
        "error": operation.error,
        "created_at": operation.created_at.isoformat(),
    }
