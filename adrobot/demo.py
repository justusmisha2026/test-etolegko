from copy import deepcopy

from adrobot.errors import AppError
from adrobot.keitaro import Record


class DemoKeitaro:
    def __init__(self) -> None:
        self.records: dict[str, list[Record]] = {
            "domains": [{"id": 1, "name": "demo.example", "state": "active"}],
            "groups": [{"id": 1, "name": "Демонстрационная группа"}],
            "traffic_sources": [{"id": 1, "name": "Демонстрационный источник"}],
            "offers": [
                {"id": 1, "name": "Оффер A", "state": "active"},
                {"id": 2, "name": "Оффер B", "state": "active"},
                {"id": 3, "name": "Оффер C", "state": "active"},
            ],
            "campaigns": [],
            "streams": [],
        }
        self.next_id = 100

    async def list_records(self, resource: str) -> list[Record]:
        return deepcopy(self.records[resource])

    async def get_record(self, resource: str, record_id: int) -> Record:
        for record in self.records[resource]:
            if record["id"] == record_id:
                return deepcopy(record)
        raise AppError("Объект демонстрационного режима не найден", 404)

    async def streams(self, campaign_id: int) -> list[Record]:
        return deepcopy(
            [
                row
                for row in self.records["streams"]
                if row["campaign_id"] == campaign_id
            ]
        )

    async def create(self, resource: str, payload: Record) -> Record:
        self.next_id += 1
        record = {**deepcopy(payload), "id": self.next_id}
        self.records[resource].append(record)
        return deepcopy(record)

    async def update(self, resource: str, record_id: int, payload: Record) -> Record:
        for row in self.records[resource]:
            if row["id"] == record_id:
                row.update(deepcopy(payload))
                return deepcopy(row)
        raise AppError("Объект демонстрационного режима не найден", 404)
