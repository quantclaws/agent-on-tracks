from demo_calc import add, multiply


def test_add_target() -> None:
    assert add(2, 3) == 5


def test_multiply_control() -> None:
    assert multiply(2, 3) == 6
