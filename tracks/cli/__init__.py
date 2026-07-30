"""CLI package; console entry point is `tracks.cli.main:main`.

No eager import of `main` here: the CLI is executed as `python -m
tracks.cli.main` (see tests/conftest.py), and importing `main` at package
import time would trigger a runpy double-import warning on stderr.
"""
