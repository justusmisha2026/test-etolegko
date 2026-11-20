import asyncio

import httpx

from adrobot.config import Settings
from adrobot.errors import AppError
from adrobot.keitaro import KeitaroClient


async def check() -> None:
    settings = Settings(demo=False)
    if message := settings.configuration_error():
        raise AppError(message)
    async with httpx.AsyncClient(
        base_url=f"{settings.keitaro_url}/admin_api/v1/",
        headers={"Api-Key": settings.api_key.get_secret_value()},
        timeout=settings.timeout_seconds,
        follow_redirects=False,
    ) as http:
        client = KeitaroClient(http)
        for resource in ("domains", "groups", "traffic_sources", "offers"):
            records = await client.list_records(resource)
            print(f"{resource}: доступно записей {len(records)}")
    print("Чтение справочников проверено. Запросы записи не выполнялись.")


def main() -> None:
    try:
        asyncio.run(check())
    except AppError as exc:
        raise SystemExit(exc.message) from None


if __name__ == "__main__":
    main()
