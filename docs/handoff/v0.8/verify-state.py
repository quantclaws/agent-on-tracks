"""Read-only handoff integrity check; does not certify product acceptance."""

import hashlib
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
data = Path(__file__).resolve().parent / "data"
state = json.loads((data / "state.json").read_text())
errors = []
for entry in state["wip_files"]:
    path = repo / entry["path"]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        errors.append(f"WIP changed or missing: {entry['path']}")
for entry in state["wip_files"]:
    saved = repo / ".tracks/runtime/handoff-assets/root-wip-files" / entry["path"]
    if not saved.is_file() or hashlib.sha256(saved.read_bytes()).hexdigest() != entry["sha256"]:
        errors.append(f"Recovery copy changed or missing: {entry['path']}")
candidate = repo / ".tracks/runtime/handoff-assets/parity-candidate"
for name, digest in json.loads((data / "parity-files.json").read_text()).items():
    path = candidate / name
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        errors.append(f"Candidate changed or missing: {name}")
for entry in json.loads((data / "cleanup-manifest.json").read_text()):
    if (repo / entry["path"]).exists():
        errors.append(f"Retired document still exists: {entry['path']}")
for name in ("README.md", "CURRENT-STATE.md", "NEXT-STEPS.md", "VERIFICATION.md", "OPERATIONS.md"):
    if not (data.parent / name).is_file():
        errors.append(f"Missing guide: {name}")
print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
raise SystemExit(bool(errors))
