"""Reviewer-only isolated parity check; never give frozen tests to implementers."""

import argparse
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--candidate", required=True, type=Path)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[3]
source = args.candidate.resolve()
work = Path(tempfile.mkdtemp(prefix="tracks-parity-review-"))
shutil.copytree(
    source / "tracks", work / "tracks", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
)
shutil.copytree(
    repo / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
)
for name in ("pyproject.toml", ".flake8"):
    shutil.copy2(repo / name, work / name)
(work / ".venv").symlink_to(repo / ".venv", target_is_directory=True)
checks = [
    "tests/integration/test_envelope_closed_contract_direct.py",
    "tests/integration/test_dispatch_parity_materialized_direct.py",
    "tests/integration/test_envelope_collection_direct.py",
    "tests/integration/test_envelope_backend_collection_direct.py",
]
with (work / "review.log").open("w") as log:
    result = subprocess.run(
        [
            str(work / ".venv/bin/python"),
            "-m",
            "pytest",
            *checks,
            "-q",
            "--tb=short",
            "--junitxml=review.xml",
        ],
        cwd=work,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=False,
    )
summary = {"candidate": str(source), "review_dir": str(work), "exit_code": result.returncode}
if (work / "review.xml").exists():
    summary["suites"] = [s.attrib for s in ET.parse(work / "review.xml").getroot()]
(work / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
raise SystemExit(result.returncode)
