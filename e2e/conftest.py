import socket
import threading
import time
from collections.abc import Iterator
from copy import deepcopy

import pytest
import uvicorn
from playwright.sync_api import Page, Route
from pydantic import SecretStr

from adrobot.config import Settings
from adrobot.demo import DemoKeitaro
from adrobot.main import create_app

STARTUP_TIMEOUT_SECONDS = 10


@pytest.fixture
def offer_bindings(request: pytest.FixtureRequest) -> list[dict[str, object]]:
    return deepcopy(
        getattr(request, "param", [{"offer_id": 1, "share": 100, "state": "active"}])
    )


@pytest.fixture
def app_url(offer_bindings: list[dict[str, object]]) -> Iterator[str]:
    gateway = DemoKeitaro()
    gateway.records["campaigns"] = [
        {"id": 101, "name": "test_demo_Seed", "state": "active"}
    ]
    gateway.records["streams"] = [
        {
            "id": 102,
            "campaign_id": 101,
            "name": "Google GEO",
            "schema": "redirect",
            "offers": [],
        },
        {
            "id": 103,
            "campaign_id": 101,
            "name": "Offers",
            "schema": "landings",
            "offers": offer_bindings,
        },
    ]
    gateway.next_id = 103
    settings = Settings(demo=True, api_key=SecretStr(""), _env_file=None)
    application = create_app(settings, gateway)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(application, log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("E2E server failed to start")
                time.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=STARTUP_TIMEOUT_SECONDS)
            if thread.is_alive():
                raise RuntimeError("E2E server failed to stop")


@pytest.fixture
def app_page(page: Page, app_url: str) -> Page:
    def local_only(route: Route) -> None:
        if route.request.url.startswith(f"{app_url}/"):
            route.continue_()
        else:
            route.abort()

    page.context.route("**/*", local_only)
    page.goto(app_url)
    return page
