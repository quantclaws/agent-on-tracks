"""Small deterministic candidate corpus for the dynamic host demo."""


def add(left: int, right: int) -> int:
    return left + right


def multiply(left: int, right: int) -> int:
    return left * right


def label(score: int) -> str:
    return "pass" if score >= 60 else "fail"
