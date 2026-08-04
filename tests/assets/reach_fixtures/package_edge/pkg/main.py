"""entry module."""
import pkg.mod_a  # noqa: F401
from pkg import sub  # noqa: F401  package-edge: from pkg import sub -> pkg.sub


def main():
    pass
