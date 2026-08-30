from host_calc import add, multiply, next_patch


def test_add_target() -> None:
    assert add(2, 3) == 5


def test_multiply_control() -> None:
    assert multiply(2, 3) == 6


def test_next_patch_first() -> None:
    assert next_patch([]) == "v0.1.0"
