"""package init: re-exports helper from a relative submodule."""
from .sub import helper  # noqa: F401  relative import -> pkg.sub
