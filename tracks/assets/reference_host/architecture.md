---
architecture_id: ARCH-REFERENCE-001
spec_ref: SPEC-008
created: 2026-08-31
status: template
sha:
---

# reference-host machine contracts

This installed-wheel asset is deployed to
`.tracks/projects/v0.1/architecture.md` of the provisioned reference host
repo (IF-REFERENCE-001). It carries the reference host's canonical quality
guard registry and no product behaviour. host = "reference-host"; required
checks are lint/coverage/test only (the reference product has no tracks-side
deliverables/trace/reach checks of its own beyond what `trac` runs on it).

## 4. Machine contracts

### 4.2 Canonical quality guard registry

```toml
[quality_registry]
version = 1
host = "reference-host"

[[quality_guard]]
id = "lint-format"
category = "lint_format"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check host_calc.py tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff", "tool.ruff.lint"]
config_digest = "sha256:5849f11e2ea3b91f2c9a205e7ec4c7633e71e0733acef7f49599f2bd5fb3078a"
scope = ["host_calc.py", "tests"]
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
command = ".venv/bin/ruff check host_calc.py tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff.lint"]
config_digest = "sha256:5849f11e2ea3b91f2c9a205e7ec4c7633e71e0733acef7f49599f2bd5fb3078a"
scope = ["host_calc.py", "tests"]
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
command = ".venv/bin/flake8 --config flake8.ini host_calc.py"
config_paths = ["flake8.ini"]
config_sections = ["flake8"]
config_digest = "sha256:e9107a5a289abb4ad43ab82cfbfcc558faeadbb44bc6be4f0ff6f873ba6fac32"
scope = ["host_calc.py"]
threshold = "CCR001 max-cognitive-complexity=15; tests exempt"
timeout_seconds = 120
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "file-length"
category = "file_length"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=C0302 host_calc.py tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.format"]
config_digest = "sha256:5849f11e2ea3b91f2c9a205e7ec4c7633e71e0733acef7f49599f2bd5fb3078a"
scope = ["host_calc.py", "tests"]
threshold = "C0302 max-module-lines=500"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "method-length-locals"
category = "method_length_locals"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0915,R0914 host_calc.py"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.design"]
config_digest = "sha256:5849f11e2ea3b91f2c9a205e7ec4c7633e71e0733acef7f49599f2bd5fb3078a"
scope = ["host_calc.py"]
threshold = "R0915 max-statements=50; R0914 max-locals=15; tests exempt"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "duplication"
category = "duplication"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0801 host_calc.py tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.similarities"]
config_digest = "sha256:5849f11e2ea3b91f2c9a205e7ec4c7633e71e0733acef7f49599f2bd5fb3078a"
scope = ["host_calc.py", "tests"]
threshold = "R0801 min-similarity-lines=5; comments/docstrings/signatures ignored"
timeout_seconds = 300
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
config_digest = "sha256:5849f11e2ea3b91f2c9a205e7ec4c7633e71e0733acef7f49599f2bd5fb3078a"
scope = ["host_calc.py"]
threshold = "line coverage >=95; by=collected; source omit=none"
timeout_seconds = 900
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
config_sections = ["unit", "integration", "e2e", "adapter", "host-contract"]
config_digest = "sha256:363076149186e5bc55ea0f19428647192f67f36b339da588e656115497ecb125"
scope = ["local-commit", "pull-request", "main", "releases"]
threshold = "no --exit-zero; required=lint,coverage,test"
timeout_seconds = 1800
failure_policy = "fail_closed"
execution_points = ["pre_commit", "ci"]
required_check = "lint,coverage,test"
```
