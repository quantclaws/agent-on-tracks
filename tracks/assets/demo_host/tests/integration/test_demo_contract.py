import pytest

from demo_calc import label


@pytest.mark.integration
def test_label_contract() -> None:
    assert label(60) == "pass"
    assert label(59) == "fail"
