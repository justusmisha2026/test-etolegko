import pytest
from playwright.sync_api import Page, expect


def open_editor(page: Page, campaign_id: str = "101", stream_id: str = "103") -> None:
    expect(page.locator("#reload")).to_be_enabled()
    page.get_by_role("button", name="Редактор офферов", exact=True).click()
    page.locator("#campaign-select").select_option(campaign_id)
    expect(page.locator("#stream-select")).to_be_enabled()
    page.locator("#stream-select").select_option(stream_id)
    expect(page.locator("#offers-editor")).to_be_visible()


def add_offer(page: Page, offer_id: str) -> None:
    page.locator("#add-offer-select").select_option(offer_id)
    page.get_by_role("button", name="Добавить оффер", exact=True).click()


def assert_shares(page: Page, values: list[str]) -> None:
    expect(page.locator("#offer-rows tr td:nth-child(3)")).to_have_text(values)


def save_offers(page: Page) -> None:
    with page.expect_response(
        lambda response: (
            response.request.method == "PUT" and response.url.endswith("/offers")
        )
    ) as saved:
        page.locator("#save-offers").click()
    assert saved.value.status == 200
    expect(page.locator("#notice")).to_have_text(
        "Состав и доли сохранены в демонстрационном хранилище."
    )
    expect(page.locator("#save-offers")).to_be_disabled()
    expect(page.locator("#campaign-select")).to_be_enabled()


def test_creation_and_operation_journal(app_page: Page) -> None:
    page = app_page
    expect(page.locator("#reload")).to_be_enabled()
    page.locator('[name="name"]').fill("E2E")
    page.locator('[name="country"]').select_option("AU")
    for name in ("offer_id", "domain_id", "group_id", "traffic_source_id"):
        page.locator(f'[name="{name}"]').select_option("1")
    page.locator("#create-submit").click()
    expect(page.locator("#notice")).to_have_text("Кампания #104 создана и проверена.")
    page.get_by_role("button", name="Журнал операций", exact=True).click()
    expect(page.locator("#operation-list strong")).to_have_text(
        "Завершено · Кампания #104"
    )
    open_editor(page, "104", "106")
    assert_shares(page, ["100%"])


def test_add_remove_persist_and_last_offer_guard(app_page: Page) -> None:
    page = app_page
    open_editor(page)
    add_offer(page, "2")
    add_offer(page, "3")
    assert_shares(page, ["33%", "33%", "34%"])
    expect(page.locator('#add-offer-select option:not([value=""])')).to_have_count(0)
    save_offers(page)
    page.reload()
    open_editor(page)
    assert_shares(page, ["33%", "33%", "34%"])
    page.locator("#offer-rows tr").nth(1).get_by_role("button").click()
    assert_shares(page, ["50%", "50%"])
    save_offers(page)
    page.locator("#offer-rows tr").nth(1).get_by_role("button").click()
    save_offers(page)
    assert_shares(page, ["100%"])
    expect(page.locator("#offer-rows button")).to_be_disabled()


@pytest.mark.parametrize(
    "offer_bindings",
    [
        [
            {"offer_id": 1, "share": 100, "state": "active"},
            {"offer_id": 2, "share": 0, "state": "disabled"},
        ]
    ],
    indirect=True,
)
def test_return_disabled_offer_matches_saved_state(app_page: Page) -> None:
    page = app_page
    open_editor(page)
    page.locator("#offer-rows tr").nth(1).get_by_role("button").click()
    add_offer(page, "2")
    add_offer(page, "3")
    assert_shares(page, ["50%", "0%", "50%"])
    expect(page.locator("#offer-rows tr").nth(1)).to_contain_text("Выключен")
    save_offers(page)
    page.reload()
    open_editor(page)
    assert_shares(page, ["50%", "0%", "50%"])
    expect(page.locator("#offer-rows tr").nth(1)).to_contain_text("Выключен")


@pytest.mark.parametrize(
    "offer_bindings",
    [
        [
            {"offer_id": 1, "share": 90, "state": "active"},
            {"offer_id": 2, "share": 10, "state": "active"},
        ]
    ],
    indirect=True,
)
def test_undo_composition_restores_original_shares(app_page: Page) -> None:
    page = app_page
    open_editor(page)
    add_offer(page, "3")
    assert_shares(page, ["33%", "33%", "34%"])
    page.locator("#offer-rows tr").nth(2).get_by_role("button").click()
    assert_shares(page, ["90%", "10%"])
    expect(page.locator("#save-offers")).to_be_disabled()
    page.locator("#offer-rows tr").nth(1).get_by_role("button").click()
    add_offer(page, "2")
    assert_shares(page, ["90%", "10%"])
    expect(page.locator("#save-offers")).to_be_disabled()


def test_stale_browser_form_cannot_overwrite_saved_offers(
    app_page: Page, app_url: str
) -> None:
    first = app_page
    second = first.context.new_page()
    try:
        second.goto(app_url)
        open_editor(first)
        open_editor(second)
        add_offer(first, "2")
        save_offers(first)
        add_offer(second, "3")
        second.locator("#save-offers").click()
        expect(second.locator("#notice")).to_contain_text("Кампания изменилась")
        expect(second.locator("#save-offers")).to_be_disabled()
        second.locator("#reload").click()
        expect(second.locator("#reload")).to_be_enabled()
        assert_shares(second, ["50%", "50%"])
        expect(second.locator("#offer-rows")).to_contain_text("Оффер B")
        expect(second.locator("#offer-rows")).not_to_contain_text("Оффер C")
    finally:
        second.close()
