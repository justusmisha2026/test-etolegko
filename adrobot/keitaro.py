from typing import Any, Protocol

import httpx

from adrobot.errors import AppError

Record = dict[str, Any]
PAGE_SIZE = 100
MAX_PAGES = 1000


class KeitaroGateway(Protocol):
    async def list_records(self, resource: str) -> list[Record]: ...

    async def get_record(self, resource: str, record_id: int) -> Record: ...

    async def streams(self, campaign_id: int) -> list[Record]: ...

    async def create(self, resource: str, payload: Record) -> Record: ...

    async def update(
        self, resource: str, record_id: int, payload: Record
    ) -> Record: ...


class KeitaroClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Record | None = None,
        params: Record | None = None,
    ) -> Any:
        writing = method != "GET"
        try:
            response = await self.http.request(
                method, path, json=payload, params=params
            )
        except httpx.RequestError as exc:
            raise AppError(
                "Keitaro недоступен. Результат записи требует сверки."
                if writing
                else "Не удалось получить ответ Keitaro",
                502,
                uncertain=writing,
            ) from exc
        if response.status_code in (401, 403):
            raise AppError("Keitaro отклонил ключ или его права", 502)
        if response.status_code == 404:
            raise AppError("Объект Keitaro не найден или недоступен", 404)
        if not response.is_success:
            raise AppError(
                f"Keitaro вернул HTTP {response.status_code}",
                502,
                uncertain=writing
                and (response.status_code >= 500 or response.status_code == 408),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AppError(
                "Keitaro вернул некорректный JSON", 502, uncertain=writing
            ) from exc

    @staticmethod
    def _record(value: Any, *, writing: bool = False) -> Record:
        if not isinstance(value, dict) or type(value.get("id")) is not int:
            raise AppError(
                "Неожиданная структура объекта Keitaro", 502, uncertain=writing
            )
        return value

    @classmethod
    def _records(cls, value: Any) -> list[Record]:
        if not isinstance(value, list):
            raise AppError("Неожиданная структура списка Keitaro", 502)
        return [cls._record(item) for item in value]

    async def list_records(self, resource: str) -> list[Record]:
        if resource != "campaigns":
            params = {"type": "campaigns"} if resource == "groups" else None
            return self._records(await self._request("GET", resource, params=params))
        records: list[Record] = []
        seen: set[int] = set()
        for page in range(MAX_PAGES):
            batch = self._records(
                await self._request(
                    "GET",
                    resource,
                    params={"offset": page * PAGE_SIZE, "limit": PAGE_SIZE},
                )
            )
            if any(item["id"] in seen for item in batch):
                raise AppError("Пагинация Keitaro вернула повторные кампании", 502)
            records.extend(batch)
            seen.update(item["id"] for item in batch)
            if len(batch) < PAGE_SIZE:
                return records
        raise AppError("Список кампаний превышает предел безопасной загрузки", 502)

    async def get_record(self, resource: str, record_id: int) -> Record:
        return self._record(await self._request("GET", f"{resource}/{record_id}"))

    async def streams(self, campaign_id: int) -> list[Record]:
        return self._records(
            await self._request("GET", f"campaigns/{campaign_id}/streams")
        )

    async def create(self, resource: str, payload: Record) -> Record:
        return self._record(
            await self._request("POST", resource, payload=payload), writing=True
        )

    async def update(self, resource: str, record_id: int, payload: Record) -> Record:
        return self._record(
            await self._request("PUT", f"{resource}/{record_id}", payload=payload),
            writing=True,
        )
