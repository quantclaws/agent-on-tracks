"""Compatibility contract for historical runs and deterministic fixtures.

New installed projects must declare their own host contract in M-DESIGN.
This no-op contract is never a production default for the full workflow.
"""

_DEFAULT_HOST_CONTRACT_TOML = """\
[host-contract]
version = 1
language = "runtime-default"
toolchain = "runtime-default"
install = "true"

[[host-contract.local_gate]]
kind = "quality"
command = "true"
result_channel = "exit_code"
timeout_seconds = 60

[[host-contract.security_scan]]
id = "runtime-default-scan"
tool = "runtime-default"
install = "true"
command = "true"
result_channel = "exit_code"
threshold = "violations=0"
timeout_seconds = 60

[host-contract.ci]
repo_env = "TRAC_GITHUB_REPO"
workflow = "ci.yml"
required_checks = []
conclusion = "success"

[host-contract.tracker]
repo_env = "TRAC_GITHUB_REPO"
project_env = "TRAC_GITHUB_PROJECT"

[host-contract.operations.feature]
steps = ["merge:main"]

[host-contract.operations.post_release]
steps = ["merge:main"]

[host-contract.operations.dev]
steps = ["merge:main"]
"""

