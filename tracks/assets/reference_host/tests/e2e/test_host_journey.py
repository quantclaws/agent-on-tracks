import host_calc


def test_public_outlet() -> None:
    assert host_calc.main() == 0
