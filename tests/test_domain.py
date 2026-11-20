import pytest

from adrobot.domain import distribute_offers
from adrobot.errors import AppError


@pytest.mark.parametrize("count", range(1, 101))
def test_distribution_is_balanced_positive_and_totals_100(count):
    offers = distribute_offers(list(range(1, count + 1)), [])
    shares = [row["share"] for row in offers]
    assert sum(shares) == 100
    assert min(shares) >= 1
    assert max(shares) - min(shares) <= 1
    assert shares == sorted(shares)


def test_disabled_offer_keeps_state_and_does_not_receive_traffic():
    current = [{"offer_id": 2, "state": "disabled", "share": 50}]
    offers = distribute_offers([1, 2, 3], current)
    assert [row["share"] for row in offers] == [50, 0, 50]
    assert offers[1]["state"] == "disabled"
    with pytest.raises(AppError, match="последний активный"):
        distribute_offers([2], current)
