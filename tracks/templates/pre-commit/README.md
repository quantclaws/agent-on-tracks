# Pre-commit template suite

This directory contains modular `.pre-commit-config.yaml` templates used by the
`tracks` toolchain.

## Files

- `base.yaml` — language-agnostic hooks from `pre-commit-hooks`.
- `python.yaml` — Python linting and type checking (ruff, mypy).
- `node.yaml` — JavaScript/TypeScript linting (eslint).
- `go.yaml` — Go formatting, tests, and linting.
- `rust.yaml` — Rust formatting and checks.
- `java.yaml` — Java formatting and static analysis.
- `ci-snippet.yml` — GitHub Actions snippet for running pre-commit in CI.

## Usage

For single-language projects, copy the language template and prepend the
contents of `base.yaml`. For multi-language projects, concatenate `base.yaml`
followed by the relevant language templates.
