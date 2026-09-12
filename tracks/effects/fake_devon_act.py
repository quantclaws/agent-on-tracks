"""FakeBackend Devon RGR phase simulation (M-IMPL, schema v2).

Extracted from ``fake.py`` for module-size compliance (C0302). Phase
assignment validation + deterministic red/green/refactor evidence; patch
generation helpers stay in ``devon_patch.py``.
"""

from __future__ import annotations

from tracks.effects.devon_patch import append_text, unified_patch
from tracks.effects.fake_shield import _FAILED_TOKENS

_DEVON_PHASES = ("red", "green", "refactor")
_DEVON_FAILURE_TOKENS = frozenset((*_FAILED_TOKENS, "hang"))



class FakeDevonActMixin:
    """Deterministic Devon phase outcomes + assignment contract validation."""

    def _act_m_impl_devon(self, substate: str, assignment: dict) -> dict:
        """Return one deterministic Devon phase outcome without filesystem I/O."""
        error = self._devon_assignment_error(assignment)
        if error is not None:
            return self._devon_contract_failure(error)
        phase = assignment["phase"]
        token = self.token("devon", phase.upper(), "ok")
        if token in _DEVON_FAILURE_TOKENS or (token == "stub_token_failure" and phase != "red"):
            result = self._devon_token_failure(assignment, phase, token)
        elif phase == "refactor":
            result = self._devon_refactor(assignment, token)
        elif phase == "red":
            result = self._devon_red(assignment, token)
        else:
            result = self._devon_green(assignment, token)
        self._devon_attach_hotfix_trailers(result, assignment)
        self._ack_injected_evidence(result, assignment)
        return result

    @staticmethod
    def _devon_assignment_error(assignment: dict) -> str | None:
        """Validate Devon's materialized assignment before deriving a patch."""
        for checker in (
            FakeDevonActMixin._devon_required_error,
            FakeDevonActMixin._devon_value_error,
            FakeDevonActMixin._devon_manifest_error,
        ):
            error = checker(assignment)
            if error is not None:
                return error
        return None

    @staticmethod
    def _devon_required_error(assignment: dict) -> str | None:
        required = (
            "task_id",
            "if_ids",
            "ac_refs",
            "test_refs",
            "commands",
            "manifest",
            "phase",
            "pre_dirty_snapshot",
            "result_identity",
        )
        missing = [key for key in required if key not in assignment or assignment[key] is None]
        phase = assignment.get("phase")
        if phase not in _DEVON_PHASES:
            missing.append("phase")
        if phase in ("green", "refactor") and not assignment.get("r_tree_identity"):
            missing.append("r_tree_identity")
        if missing:
            return "missing or invalid: " + ", ".join(dict.fromkeys(missing))
        return None

    @staticmethod
    def _devon_value_error(assignment: dict) -> str | None:
        phase = assignment["phase"]
        if not isinstance(assignment["task_id"], str) or not assignment["task_id"].strip():
            return "task_id must be a non-empty string"
        for key in ("if_ids", "ac_refs", "test_refs"):
            if not FakeDevonActMixin._devon_string_list(assignment[key]):
                return f"{key} must be a non-empty string list"
        if not FakeDevonActMixin._devon_commands_valid(assignment["commands"]):
            return "commands must contain at least one command"
        snapshot = assignment["pre_dirty_snapshot"]
        if (
            not isinstance(snapshot, (dict, list, tuple, str))
            or isinstance(snapshot, str)
            and not snapshot.strip()
            or isinstance(snapshot, (list, tuple))
            and not snapshot
        ):
            return "pre_dirty_snapshot has an invalid type"
        if (
            not isinstance(assignment["result_identity"], str)
            or not assignment["result_identity"].strip()
        ):
            return "result_identity must be a non-empty string"
        if phase in ("green", "refactor") and (
            not isinstance(assignment["r_tree_identity"], str)
            or not assignment["r_tree_identity"].strip()
        ):
            return "r_tree_identity must be a non-empty string"
        return None

    @staticmethod
    def _devon_manifest_error(assignment: dict) -> str | None:
        manifest = assignment["manifest"]
        if not isinstance(manifest, dict):
            return "manifest must be an object"
        for key in ("allowed_paths", "forbidden_paths"):
            paths_value = manifest.get(key)
            if not FakeDevonActMixin._devon_manifest_paths_valid(paths_value):
                return f"manifest.{key} must be a non-empty path list"
        # B50 (#65): schema-2 assignments carry the split contract -- unit_refs
        # (unit paths, may be empty: the fake Devon synthesizes its own RED
        # test) and acceptance_refs (integration paths). Legacy assignments
        # keep the unit-only test_refs rule.
        unit_refs = assignment.get("unit_refs")
        acceptance_refs = assignment.get("acceptance_refs")
        if unit_refs is not None or acceptance_refs is not None:
            if unit_refs and not FakeDevonActMixin._devon_test_refs_valid(unit_refs):
                return "unit_refs must target repo-relative tests/unit paths"
            if acceptance_refs and not all(
                str(ref).split("::", 1)[0].replace("\\", "/").startswith(
                    "tests/integration/"
                )
                for ref in acceptance_refs
            ):
                return "acceptance_refs must target repo-relative tests/integration paths"
        elif not FakeDevonActMixin._devon_test_refs_valid(assignment["test_refs"]):
            return "test_refs must target repo-relative tests/unit paths"
        return None

    @staticmethod
    def _devon_string_list(value: object) -> bool:
        return (
            isinstance(value, (list, tuple))
            and bool(value)
            and all(
                isinstance(item, str) and item.strip() and "\n" not in item and "\r" not in item
                for item in value
            )
        )

    @staticmethod
    def _devon_commands_valid(value: object) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, dict):
            return bool(value) and all(
                FakeDevonActMixin._devon_commands_valid(item) for item in value.values()
            )
        if isinstance(value, (list, tuple)):
            return bool(value) and all(
                FakeDevonActMixin._devon_commands_valid(item) for item in value
            )
        return False

    @staticmethod
    def _devon_manifest_paths_valid(value: object) -> bool:
        return (
            isinstance(value, (list, tuple))
            and bool(value)
            and all(FakeDevonActMixin._devon_manifest_path_valid(item) for item in value)
        )

    @staticmethod
    def _devon_manifest_path_valid(value: object) -> bool:
        if not isinstance(value, str):
            return False
        path = value.strip().replace("\\", "/")
        return not (
            not path
            or path == "."
            or path.startswith("/")
            or any(char.isspace() for char in path)
            or "::" in path
            or ".." in path.split("/")
            or ("*" in path and not path.endswith("/**"))
        )

    @staticmethod
    def _devon_test_refs_valid(value: object) -> bool:
        if not FakeDevonActMixin._devon_string_list(value):
            return False
        return all(
            path.split("::", 1)[0].replace("\\", "/").startswith("tests/unit/")
            and ".." not in path.split("::", 1)[0].split("/")
            for path in value
        )

    @staticmethod
    def _devon_contract_failure(reason: str) -> dict:
        return {
            "status": "failed",
            "artifact_ref": None,
            "failure_class": "contract_error",
            "audit_evidence": f"contract_error: {reason}",
            "self_report": f"Devon assignment rejected: {reason}",
        }

    def _devon_token_failure(self, assignment: dict, phase: str, token: str) -> dict:
        failure_class = "agent_failed" if token == "fail" else token
        if token == "hang":
            failure_class = "timeout"
        summary = f"simulated Devon {phase} failure: {token}"
        evidence = self._devon_evidence(
            assignment,
            phase,
            [],
            self._devon_commands(assignment, "", "fail", summary),
            [{"status": "fail", "classification": token, "output_summary": summary}],
            f"failure:{token}",
            [],
        )
        return {
            "status": "failed",
            "artifact_ref": None,
            "failure_class": failure_class,
            "audit_evidence": summary,
            "self_report": summary,
            **evidence,
        }

    def _devon_red(self, assignment: dict, token: str) -> dict:
        red_content, error = self._devon_red_content(assignment, token)
        if error is not None:
            return self._devon_contract_failure(error)
        test_path, body, verdict, summary = red_content
        existing_test, error = self._devon_existing_text(test_path)
        if error is not None:
            return self._devon_contract_failure(error)
        updated = append_text(existing_test, body)
        patch = unified_patch(test_path, existing_test, updated)
        evidence = self._devon_evidence(
            assignment,
            "red",
            [test_path],
            self._devon_commands(assignment, test_path, "fail", summary),
            [{"status": "fail", "classification": verdict, "output_summary": summary}],
            patch,
            list(assignment["if_ids"]),
        )
        result = {
            "status": "done",
            "artifact_ref": None,
            "diff_ref": patch,
            "self_report": f"fake Devon red produced {verdict}",
            "verdict": verdict,
            **evidence,
        }
        if token == "stub_token_failure":
            result["failure_class"] = "stub_token_failure"
        return result

    def _devon_red_content(
        self,
        assignment: dict,
        token: str,
    ) -> tuple[tuple[str, str, str, str] | None, str | None]:
        test_path, error = self._devon_test_path(assignment)
        if error is not None:
            return None, error
        production_path = self._devon_production_reference(assignment)
        existing, error = self._devon_existing_text(production_path)
        if error is not None:
            return None, error
        implementation = self._devon_implementation_text(
            existing,
            assignment["if_ids"][0],
        )
        if token == "stub_token_failure":
            body = self._devon_stub_test(assignment["if_ids"][0])
            verdict = "stub_token_failure"
            summary = f'stub_token_failure: NotImplementedError("{assignment["if_ids"][0]}")'
        else:
            body = self._devon_assertion_test(
                production_path,
                assignment["if_ids"][0],
                implementation,
            )
            verdict = "assertion_failure"
            summary = "assertion_failure: deterministic behavioral assertion"
        return (test_path, body, verdict, summary), None

    def _devon_green(self, assignment: dict, token: str) -> dict:
        production_path, error = self._devon_production_path(assignment)
        if error is not None:
            return self._devon_contract_failure(error)
        existing, error = self._devon_existing_text(production_path)
        if error is not None:
            return self._devon_contract_failure(error)
        updated = append_text(
            existing,
            self._devon_implementation_text(
                None,
                assignment["if_ids"][0],
            ),
        )
        patch = unified_patch(production_path, existing, updated)
        summary = f"pass: minimal implementation for {assignment['if_ids'][0]}"
        evidence = self._devon_evidence(
            assignment,
            "green",
            [production_path],
            self._devon_commands(assignment, production_path, "pass", summary),
            [{"status": "pass", "classification": "implementation", "output_summary": summary}],
            patch,
            list(assignment["if_ids"]),
        )
        return {
            "status": "done",
            "artifact_ref": None,
            "diff_ref": patch,
            "self_report": "fake Devon green produced minimal implementation",
            **evidence,
        }

    def _devon_refactor(self, assignment: dict, token: str) -> dict:
        if token == "lie_no_change":
            production_path, error = self._devon_production_path(assignment)
            if error is not None:
                return self._devon_contract_failure(error)
            existing, error = self._devon_existing_text(production_path)
            if error is not None:
                return self._devon_contract_failure(error)
            target = self._repo_root() / production_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                append_text(existing, "# undeclared refactor drift\n"),
                encoding="utf-8",
            )
            reason = "no authorized behavior-preserving refactor is required"
            summary = f"pass: {reason}"
            evidence = self._devon_evidence(
                assignment,
                "refactor",
                [],
                self._devon_commands(assignment, "", "pass", summary),
                [{"status": "pass", "classification": "no_change", "output_summary": summary}],
                "no-change",
                list(assignment["if_ids"]),
                reason,
            )
            return {
                "status": "done",
                "artifact_ref": None,
                "self_report": "fake Devon lied about an undeclared refactor change",
                **evidence,
            }
        if token == "change":
            production_path, error = self._devon_production_path(assignment)
            if error is not None:
                return self._devon_contract_failure(error)
            existing, error = self._devon_existing_text(production_path)
            if error is not None:
                return self._devon_contract_failure(error)
            updated = append_text(existing, "# behavior-preserving refactor\n")
            patch = unified_patch(production_path, existing, updated)
            target = self._repo_root() / production_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(updated, encoding="utf-8")
            summary = "pass: behavior-preserving refactor"
            evidence = self._devon_evidence(
                assignment,
                "refactor",
                [production_path],
                self._devon_commands(assignment, production_path, "pass", summary),
                [{"status": "pass", "classification": "refactor", "output_summary": summary}],
                patch,
                list(assignment["if_ids"]),
            )
            return {
                "status": "done",
                "artifact_ref": None,
                "diff_ref": patch,
                "self_report": "fake Devon refactor changed implementation shape",
                **evidence,
            }
        reason = "no authorized behavior-preserving refactor is required"
        summary = f"pass: {reason}"
        evidence = self._devon_evidence(
            assignment,
            "refactor",
            [],
            self._devon_commands(assignment, "", "pass", summary),
            [{"status": "pass", "classification": "no_change", "output_summary": summary}],
            "no-change",
            list(assignment["if_ids"]),
            reason,
        )
        return {
            "status": "done",
            "artifact_ref": None,
            "self_report": f"fake Devon refactor: {reason}",
            **evidence,
        }
