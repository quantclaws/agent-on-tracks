"""Reference host source corpus: a small version-aware calculator."""


def add(left: int, right: int) -> int:
    return left + right


def multiply(left: int, right: int) -> int:
    return left * right


def next_patch(tags: list[str]) -> str:
    """Lowest unused patch tag of the form vMAJOR.MINOR.PATCH."""
    used: set[int] = set()
    for tag in tags:
        parts = tag.lstrip("v").split(".")
        if len(parts) == 3 and parts[0] == "0" and parts[1] == "1":
            used.add(int(parts[2]))
    patch = 0
    while patch in used:
        patch += 1
    return f"v0.1.{patch}"


def main() -> int:
    print("tracks-reference-host")
    return 0
