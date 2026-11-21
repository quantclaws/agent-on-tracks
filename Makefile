.PHONY: lint fast test-opencode test-install test-core test-full wheel

PY := .venv/bin/python
RUFF := .venv/bin/ruff

lint:
	$(RUFF) check tracks tests
	sh .githooks/pre-commit

# Fast lane for daily development & hotfixes (~10-15s)
fast:
	$(RUFF) check tracks tests
	$(PY) -m pytest -q -n 4 --dist loadscope \
		tests/unit/test_opencode_*.py \
		tests/unit/test_install_cmd_red.py \
		tests/unit/test_registry_inheritance.py \
		tests/unit/test_installed_workflow_profile.py \
		tests/unit/test_check_trace.py \
		tests/integration/test_opencode_*.py \
		tests/integration/test_install_package.py \
		tests/integration/test_install_targets.py

test-opencode:
	$(PY) -m pytest -q tests/unit/test_opencode_*.py tests/integration/test_opencode_*.py

test-install:
	$(PY) -m pytest -q tests/unit/test_install_cmd_red.py tests/integration/test_install_*.py tests/integration/test_host_environment.py tests/e2e/test_install_journey.py

test-core:
	$(PY) -m pytest -q -n 4 --dist loadscope tests/unit

test-full:
	$(PY) -m pytest -q -n 4 --dist loadscope -m 'not performance and not ui' tests/unit tests/integration tests/e2e

wheel:
	$(PY) -m pip wheel --no-deps --no-build-isolation -w dist .
