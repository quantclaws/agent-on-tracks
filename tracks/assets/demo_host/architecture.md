---
architecture_id: ARCH-DEMO-001
spec_ref: SPEC-007
created: 2026-08-24
status: template
sha:
---

# demo-pytest host machine contracts

This installed-wheel asset is copied to `.tracks/projects/v0.1/architecture.md`.
It contains the demo host's canonical quality guard registry and no product behaviour.

## 4. Machine contracts

### 4.2 Canonical quality guard registry

```toml
[quality_registry]
version = 1
host = "demo-pytest"

[[quality_guard]]
id = "lint-format"
category = "lint_format"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check demo_calc.py tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff", "tool.ruff.lint"]
config_digest = "sha256:0e05b6937ca2be83776a740638a71374199355ed76fa506accb1ba94020e6f02"
scope = ["demo_calc.py", "tests"]
threshold = "line-length=100; select=E,F,W,I,B,UP,SIM,C4; violations=0"
timeout_seconds = 120
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "static-semantic"
category = "static_analysis"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check demo_calc.py tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff.lint"]
config_digest = "sha256:0e05b6937ca2be83776a740638a71374199355ed76fa506accb1ba94020e6f02"
scope = ["demo_calc.py", "tests"]
threshold = "F and B semantic rule families; violations=0"
timeout_seconds = 120
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "cognitive-complexity"
category = "cognitive_complexity"
tool = "flake8+flake8-cognitive-complexity"
tool_version = "7.3.0+0.1.0"
command = ".venv/bin/flake8 --config flake8.ini demo_calc.py"
config_paths = ["flake8.ini"]
config_sections = ["flake8"]
config_digest = "sha256:3c05476e15d87c1e122f206ea4e18bf497d942347648c77f2a4f4af8358031cc"
scope = ["demo_calc.py"]
threshold = "CCR001 max-cognitive-complexity=15"
timeout_seconds = 120
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "file-length"
category = "file_length"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=C0302 demo_calc.py tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.format"]
config_digest = "sha256:0e05b6937ca2be83776a740638a71374199355ed76fa506accb1ba94020e6f02"
scope = ["demo_calc.py", "tests"]
threshold = "C0302 max-module-lines=500"
timeout_seconds = 120
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "method-length-locals"
category = "method_length_locals"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0915,R0914 demo_calc.py"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.design"]
config_digest = "sha256:0e05b6937ca2be83776a740638a71374199355ed76fa506accb1ba94020e6f02"
scope = ["demo_calc.py"]
threshold = "R0915 max-statements=50; R0914 max-locals=15"
timeout_seconds = 120
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "duplication"
category = "duplication"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0801 demo_calc.py tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.similarities"]
config_digest = "sha256:0e05b6937ca2be83776a740638a71374199355ed76fa506accb1ba94020e6f02"
scope = ["demo_calc.py", "tests"]
threshold = "R0801 min-similarity-lines=5"
timeout_seconds = 120
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "coverage-threshold"
category = "coverage_threshold"
tool = "coverage+pytest"
tool_version = "7.15.2+9.1.1"
command = ".venv/bin/coverage report --fail-under=95"
config_paths = ["pyproject.toml"]
config_sections = ["tool.coverage.run", "tool.coverage.report"]
config_digest = "sha256:0e05b6937ca2be83776a740638a71374199355ed76fa506accb1ba94020e6f02"
scope = ["demo_calc.py"]
threshold = "line coverage >=95; by=collected; source omit=none"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "ci"]
required_check = "coverage"

[[quality_guard]]
id = "hooks-runner-ci-required"
category = "hooks_runner_ci_required_checks"
tool = "git-hooks+github-actions"
tool_version = "git-env-fingerprinted+checkout@v4+setup-python@v5"
command = "sh .githooks/pre-commit"
config_paths = [".tracks/projects/project.toml"]
config_sections = ["unit", "integration", "e2e", "adapter"]
config_digest = "sha256:103a00417d99e2907583b3852c3e98cedb0f92f9b1afa987a3a92d32680e5de8"
scope = ["local-commit", "pull-request"]
threshold = "no --exit-zero; required=lint,coverage,test"
timeout_seconds = 900
failure_policy = "fail_closed"
execution_points = ["pre_commit", "ci"]
required_check = "lint,coverage,test"
```
