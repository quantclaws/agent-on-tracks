from host_calc import add, next_patch


def test_contract_happy() -> None:
    assert add(1, 1) == 2


def test_next_patch_increments() -> None:
    assert next_patch(["v0.1.0"]) == "v0.1.1"
