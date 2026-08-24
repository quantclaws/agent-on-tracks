import pytest

from demo_calc import add, label


@pytest.mark.e2e
def test_demo_happy_journey() -> None:
    assert label(add(30, 30)) == "pass"
