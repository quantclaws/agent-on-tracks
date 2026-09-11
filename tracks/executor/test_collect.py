"""M-TEST layer collection and baseline capture extracted from the Executor
(mixin ``ExecTestCollectMixin``)."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from tracks.executor.helpers import parse_collected_nodes
from tracks.executor.host_contract import declared_install_interpreter
from tracks.executor.test_select import (
    BaselineAssets,
    TestSelectError,
    capture_test_baseline,
    classify_nodes,
    collect_node_source_digests,
)
from tracks.project import ContractError, load_contract

# D-41 (v6 review pin): test exit code that means an EMPTY layer at collect
# time is ONLY 5 ("no tests collected"). rc=4 is a usage/path error -- a
# broken declaration that must fail closed naming the layer, never silently
# contribute zero nodes (a fresh feature project legitimately has zero unit/
# e2e nodes via rc=5; an un-collectable layer never does).
_EMPTY_LAYER_COLLECT_RC = frozenset({5})


# D-41 selection scope/basis for M-TEST RED_CHECK (interfaces §1a/§1j).
_R2_SCOPE = "r2_delta"


_R2_BASIS = "delta-declaration"


def _contract_sections(contract):
    """The required flat test layers in canonical order (unit, integration,
    e2e); the activated atomic schema declares all three -- the loader fails
    closed on any missing layer section."""
    return [
        ("unit", contract.unit),
        ("integration", contract.integration),
        ("e2e", contract.e2e),
    ]


def _resolve_contract_argv0(argv: list[str], cwd: Path) -> list[str]:
    """Resolve a contract command's argv[0] against the project cwd.

    Live replay fix, contract-driven (IF-HOSTCONTRACT-001, NFR-0147): the
    recognizer for a substitutable env interpreter comes from the host
    contract's declared install interpreter (first token of ``install``;
    the boundary default when no contract loads) — this module never
    spells an interpreter itself. When argv[0] IS the declared env
    interpreter and no file of that relative path exists under cwd (a
    worktree created from a host that carries its environment, replayed
    where it does not), substitute the Runtime's own ``sys.executable`` —
    but ONLY when that executable is itself running inside an isolated
    environment (so we never fall back to a system Python). A worktree
    interpreter that exists is authoritative and is used verbatim;
    non-matching commands, absolute paths, and other missing executables
    keep their original subprocess error / contract-failure semantics.
    """
    if not argv:
        return argv
    first = PurePosixPath(argv[0])
    if first.is_absolute() or posixpath.normpath(argv[0]) != argv[0]:
        return argv
    if argv[0] != posixpath.normpath(declared_install_interpreter(cwd)):
        return argv
    if (cwd / argv[0]).exists():
        return argv
    if sys.prefix != sys.base_prefix and os.access(sys.executable, os.X_OK):
        return [sys.executable, *argv[1:]]
    return argv


class ExecTestCollectMixin:
    """Layer collection / baseline capture; the R2 selection constants and contract
argv helpers are defined here for the test-run mixin and phase0 repair."""

    def _run_contract_sections(
        self, cmd, state, field: str
    ) -> tuple[list[tuple[str, int, str, str]], str | None]:
        """Execute the contract's ``collect``/``run`` command across the
        declared Shield layers ([integration] + optional [e2e]) -- the legacy
        M-TEST/M-IMPL section set. Returns ``(results, error_msg)`` where
        ``results`` is a list of ``(section_name, rc, stdout, stderr)`` per
        section. On contract/shlex error ``error_msg`` is set and ``results``
        is empty."""
        try:
            contract = load_contract(self.repo)
        except ContractError as exc:
            return [], f"contract error: {exc.reason}"
        sections = [("integration", contract.integration)]
        if contract.e2e is not None:
            sections.append(("e2e", contract.e2e))
        results: list[tuple[str, int, str, str]] = []
        for name, section in sections:
            try:
                argv = shlex.split(getattr(section, field))
            except ValueError as exc:
                return [], f"contract {field} command invalid: {exc}"
            cwd = self.repo / section.cwd if section.cwd != "." else self.repo
            argv = _resolve_contract_argv0(argv, cwd)
            proc = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
            )
            rc = proc.returncode
            # M-TEST collection: test exit_code=5 ("no tests collected")
            # on the e2e section is not a collection failure — a hotfix run
            # with an integration-only delta has no e2e tests. Normalize to
            # 0 for collect only so _do_collect_tests does not hard-fail.
            # (D-41 full-inventory paths use _collect_all_declared_layers,
            # where ONLY rc=5 is a legal empty layer and rc=4 fails closed.)
            if field == "collect" and name == "e2e" and rc == 5:
                rc = 0
            results.append((name, rc, proc.stdout, proc.stderr))
        return results, None

    def _collect_all_declared_layers(
        self, *, capture_absent_ok: bool = False
    ) -> tuple[dict[str, str] | None, str | None]:
        """D-41 FR-0250-01: run EVERY declared layer's ``collect`` command
        ([unit]/[integration]/[e2e]) and map each collected node id to its
        declaring layer. Only rc=5 ("no tests collected") is a legal empty
        declared layer; any other failure (incl. rc=4 usage/path errors) is
        returned as an error string naming the layer (fail-closed). A nodeid
        collected by MULTIPLE layers is a contract defect and fails the scan
        closed -- first-layer-wins masking would silently hide it.

        ``capture_absent_ok=True`` (pre-WRITE capture only, FRB-D): when ALL
        of a section's declared ``paths`` (resolved under the section cwd)
        are absent from disk, the declared layer normalizes to EMPTY without
        executing its collect command -- a valid contract may legitimately
        declare paths a fresh tree does not have yet. The cwd itself must
        exist and be a directory for that normalization: a missing/non-
        directory section cwd is malformed infrastructure and routes a layer
        collection error (baseline_defect at capture) WITHOUT subprocess. If
        ANY declared path exists the command executes and rc=4 stays a
        failure. Post-WRITE COLLECT never normalizes (default False)."""
        try:
            contract = load_contract(self.repo)
            sections = _contract_sections(contract)
        except ContractError as exc:
            return None, f"contract error: {exc.reason}"
        node_layer: dict[str, str] = {}
        errors: list[str] = []
        for name, section in sections:
            fatal_error = self._collect_declared_layer(
                name, section, capture_absent_ok, node_layer, errors
            )
            if fatal_error is not None:
                return None, fatal_error
        if errors:
            return None, "; ".join(errors)
        return node_layer, None

    def _collect_declared_layer(
        self,
        name: str,
        section,
        capture_absent_ok: bool,
        node_layer: dict[str, str],
        errors: list[str],
    ) -> str | None:
        """Collect ONE declared layer into ``node_layer`` (D-41 FR-0250-01).

        Per-layer collection failures accumulate into ``errors`` (fail-closed
        at the caller); a returned string is a FATAL scan error (the layer's
        ``collect`` command itself is unparseable) that aborts immediately."""
        try:
            argv = shlex.split(section.collect)
        except ValueError as exc:
            return f"[{name}].collect command invalid: {exc}"
        cwd = self.repo / section.cwd if section.cwd != "." else self.repo
        if capture_absent_ok and self._capture_layer_absent(name, section, cwd, errors):
            return None
        argv = _resolve_contract_argv0(argv, cwd)
        try:
            proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        except (OSError, UnicodeError) as exc:
            # FRB-G: a missing executable / undecodable stream is a layer
            # collection failure routed through the event channel, never
            # a raw crash out of the handler.
            errors.append(
                f"{name} layer collection failed ({type(exc).__name__}): {exc}"
            )
            return None
        self._absorb_layer_nodes(name, proc, node_layer, errors)
        return None

    def _capture_layer_absent(self, name, section, cwd: Path, errors: list[str]) -> bool:
        """FRB-D pre-WRITE normalization: True when the declared layer must be
        treated as EMPTY without executing its collect command. A missing/non-
        directory section cwd is malformed infrastructure (every declared path
        reads absent as a side effect) -- routes a layer collection error
        WITHOUT any subprocess. If ANY declared path exists the command must
        execute (rc=4 stays a failure)."""
        if not cwd.is_dir():
            errors.append(
                f"{name} layer collection failed: section cwd is "
                f"missing or not a directory: {section.cwd}"
            )
            return True
        return bool(section.paths) and not any((cwd / rel).exists() for rel in section.paths)

    @staticmethod
    def _absorb_layer_nodes(
        name: str, proc: subprocess.CompletedProcess, node_layer: dict[str, str],
        errors: list[str],
    ) -> None:
        """Fold one collect result into ``node_layer``: rc=0 maps each
        collected node to this layer (a nodeid claimed by MULTIPLE layers is a
        contract defect recorded in ``errors``), rc in _EMPTY_LAYER_COLLECT_RC
        is a legal empty declared layer, anything else fails the layer closed."""
        if proc.returncode == 0:
            for node in parse_collected_nodes(proc.stdout):
                previous = node_layer.get(node)
                if previous is not None and previous != name:
                    errors.append(
                        f"{node} collected by multiple layers ({previous}, {name}): "
                        "layer ownership must be unique"
                    )
                    continue
                node_layer[node] = name
        elif proc.returncode not in _EMPTY_LAYER_COLLECT_RC:
            detail = (proc.stderr or proc.stdout).strip()
            errors.append(
                f"{name} layer collection failed (rc={proc.returncode}): {detail[:400]}"
            )

    def _read_runtime_blob(self, ref: str | None):
        """Read a repo-relative ``.tracks/runtime/blobs/...`` reference."""
        if not ref:
            raise TestSelectError("event payload lacks its runtime blob reference")
        try:
            return json.loads((Path(self.repo) / ref).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TestSelectError(f"runtime blob unreadable: {ref}: {exc}") from exc

    def _emit_baseline_failed(self, cmd, state, errors: list[str]) -> None:
        """test.baseline_captured(failed) + fail-closed upstream routing
        (interfaces §1j v5: never vacate R1, never re-dispatch Shield)."""
        self._emit(
            "test.baseline_captured",
            {
                "status": "failed",
                "baseline_id": "",
                "baseline_tree": "",
                "layers": ["unit", "integration", "e2e"],
                "nodes_count": 0,
                "empty_baseline": False,
                "node_digest_blob": None,
                "errors": errors,
            },
            command_id=cmd.command_id,
        )
        log_ref = self._red_log_blob_ref({"errors": errors})
        self._emit(
            "verdict.failed",
            {
                "check": "baseline_defect",
                "target_stage": "M-DESIGN",
                "artifact_disposition": "rollback",
                "reason": (
                    "pre-WRITE R1 baseline capture failed: the inherited tree "
                    "is un-collectable/un-importable before Shield writes"
                ),
                "evidence": errors,
                "log_ref": log_ref,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _do_capture_baseline(self, cmd, state, task_id, reconcile):
        """D-41/IF-SELECT-001 (v5): M-TEST entry pre-WRITE R1 snapshot.

        Runs BEFORE this run's first Shield WRITE dispatch: full collect of
        every DECLARED layer ([unit]/[integration]/[e2e]), per-node source
        digests over physical function segments (never whole files), stamped
        baseline/tree identity persisted as ``test.baseline_captured`` plus a
        node-digest blob. Any collection/infrastructure failure fails closed
        before WRITE (baseline_defect -> upstream/design route). WAL/replay
        reuse the same stamped capture: a crashed capture re-runs only while
        no passed capture is persisted, and recomputes the identical identity.
        """
        if reconcile and state.baseline_captured:
            return
        node_layer, collect_error = self._collect_all_declared_layers(
            capture_absent_ok=True
        )
        if collect_error is not None:
            self._emit_baseline_failed(cmd, state, [collect_error])
            return
        all_nodes = sorted(node_layer)
        try:
            digests = collect_node_source_digests(Path(self.repo), all_nodes)
        except TestSelectError as exc:
            self._emit_baseline_failed(cmd, state, [str(exc)])
            return
        # FRB-E: the baseline tree identity is dirty-aware -- a clean relevant
        # tree stamps HEAD, an uncommitted source/test tree stamps head+dirty
        # content so two distinct uncommitted trees never share one snapshot.
        tree_identity = self._dirty_tree_stamp()
        snapshot = capture_test_baseline(
            lambda layer: sorted(n for n in all_nodes if node_layer[n] == layer),
            lambda node: digests[node],
            tree_identity,
        )
        # Blob entries carry BOTH identities: `digest` stamps the node's
        # physical file content (externally verifiable), `node_digest` is the
        # per-node AST segment digest classification consumes.
        file_digests: dict[str, str] = {}
        entries = []
        for node in all_nodes:
            physical = node.partition("::")[0]
            if physical not in file_digests:
                try:
                    file_digests[physical] = hashlib.sha256(
                        (Path(self.repo) / physical).read_bytes()
                    ).hexdigest()
                except OSError:
                    file_digests[physical] = "unreadable"
            entries.append(
                {
                    "node": node,
                    "layer": node_layer[node],
                    "digest": file_digests[physical],
                    "node_digest": digests[node],
                }
            )
        blob_ref = self.store.write_audit_blob(entries)
        if blob_ref is None:
            self._emit_baseline_failed(cmd, state, ["node digest blob write failed"])
            return
        self._emit(
            "test.baseline_captured",
            {
                "status": "passed",
                "baseline_id": snapshot.baseline_id,
                "baseline_tree": snapshot.baseline_tree,
                "layers": list(snapshot.layers),
                "nodes_count": len(snapshot.node_digests),
                "empty_baseline": snapshot.empty_baseline,
                "node_digest_blob": f".tracks/runtime/blobs/{blob_ref}",
                "errors": [],
            },
            command_id=cmd.command_id,
        )

    def _do_collect_tests(self, cmd, state, task_id, reconcile):
        """SM-01.5 / D-41 FR-0250-01: Runtime independently collects ALL
        declared layers via the host project contract's ``collect`` command
        (architecture.md §3.2; never trusts the Shield self-report), then
        classifies the current three-layer inventory against THIS run's
        persisted pre-WRITE ``test.baseline_captured`` snapshot.

        REMOVED (a baseline node absent from full collect) is fail-closed test-
        asset deletion: test.collected(failed, error_class=asset_deleted);
        it never silently de-registers nor continues gating. The per-node
        {node, layer, class} table persists to a blob referenced by
        ``per_node_blob`` for RED_CHECK's SELECT_R2 binding."""
        if reconcile and state.test_collected:
            return
        node_layer, collect_error = self._collect_all_declared_layers()
        if collect_error is not None:
            self._emit(
                "test.collected",
                {"status": "failed", "collected_count": 0, "errors": [collect_error]},
                command_id=cmd.command_id,
            )
            return
        all_nodes = sorted(node_layer)
        baseline_ev = self._latest_event("test.baseline_captured", status="passed")
        if baseline_ev is None:
            self._emit(
                "test.collected",
                {
                    "status": "failed",
                    "collected_count": len(all_nodes),
                    "failures": [],
                    "errors": [
                        "missing test.baseline_captured(passed): classification has "
                        "no pre-WRITE R1 snapshot (MissingBaselineCaptureError)"
                    ],
                },
                command_id=cmd.command_id,
            )
            return
        try:
            baseline_entries = self._read_runtime_blob(
                baseline_ev.payload.get("node_digest_blob")
            )
            baseline_assets = BaselineAssets(
                nodes=frozenset(entry["node"] for entry in baseline_entries),
                node_digests={entry["node"]: entry["node_digest"] for entry in baseline_entries},
            )
            digests = collect_node_source_digests(Path(self.repo), all_nodes)
        except (TestSelectError, OSError, ValueError, KeyError, TypeError) as exc:
            self._emit(
                "test.collected",
                {
                    "status": "failed",
                    "collected_count": len(all_nodes),
                    "failures": [],
                    "errors": [str(exc)],
                },
                command_id=cmd.command_id,
            )
            return
        classes = classify_nodes(baseline_assets, all_nodes, digests)
        removed = sorted(node for node, klass in classes.items() if klass == "removed")
        if removed:
            failures = [{"node": node, "error_class": "asset_deleted"} for node in removed]
            errors = [
                f"test asset deleted since baseline snapshot: {node}" for node in removed
            ]
            self._emit(
                "test.collected",
                {
                    "status": "failed",
                    "collected_count": len(all_nodes),
                    "removed": len(removed),
                    "failures": failures,
                    "errors": errors,
                    # FRB-K2 pairing marker: the companion verdict.failed
                    # (test_defect) emitted below by THIS command is the
                    # pair's single attempt charge; the kernel skips the
                    # collect-side consume for marked payloads only.
                    "companion": "test_defect",
                },
                command_id=cmd.command_id,
            )
            # FA-3 (final review pin): the failed collect alone leaves the
            # router without actionable evidence (blind re-dispatch); emit
            # the test_defect verdict naming every deleted asset BEFORE any
            # Shield rewrite so upstream routes a pinpointed rewrite.
            log_ref = self._red_log_blob_ref({"errors": errors, "failures": failures})
            self._emit(
                "verdict.failed",
                {
                    "check": "test_defect",
                    "target_stage": "M-TEST",
                    "artifact_disposition": "rewrite",
                    "reason": (
                        "REMOVED test assets deleted since the pre-WRITE baseline "
                        f"snapshot: {', '.join(removed)}"
                    ),
                    "evidence": failures,
                    "log_ref": log_ref,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        inherited_r1 = sum(1 for klass in classes.values() if klass == "r1")
        delta_r2 = sum(1 for klass in classes.values() if klass == "r2")
        per_node = [
            {"node": node, "layer": node_layer.get(node, ""), "class": classes[node]}
            for node in all_nodes
        ]
        per_ref = self.store.write_audit_blob(per_node)
        if per_ref is None:
            # Fail closed: a passed classification whose per_node_blob is null
            # has no replayable evidence and RED_CHECK would have no
            # authoritative input (review pin: never passed + null ref).
            self._emit(
                "test.collected",
                {
                    "status": "failed",
                    "collected_count": len(all_nodes),
                    "failures": [],
                    "errors": ["per-node classification blob write failed"],
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "test.collected",
            {
                "status": "passed",
                "collected_count": len(all_nodes),
                "inherited_r1": inherited_r1,
                "delta_r2": delta_r2,
                "removed": 0,
                "failures": [],
                "per_node_blob": f".tracks/runtime/blobs/{per_ref}",
                "errors": [],
            },
            command_id=cmd.command_id,
        )

    def _red_log_blob_ref(self, payload: dict) -> str | None:
        """B21/#23 (PRISM-B21-R1-01/R1-03): persist the full RED logs and
        return a fixer-usable blobs PATH (the m_impl_runtime convention), or
        None when the best-effort write fails — callers pass it through."""
        ref = self.store.write_audit_blob(payload)
        if not ref:
            return None
        return f".tracks/runtime/blobs/{ref}"

    def _selection_context(self):
        """Load RED_CHECK's selection inputs from persisted events (D-41).

        Returns ``(baseline_id, selected_by_layer, selected_all)`` where the
        R2 nodes come from COLLECT's persisted per-node classification blob
        keyed by their declared layer. Raises TestSelectError when this run
        has no persisted passed baseline capture / classification (fail-closed;
        never re-guesses from the mutable tree)."""
        collected_ev = self._latest_event("test.collected", status="passed")
        baseline_ev = self._latest_event("test.baseline_captured", status="passed")
        if collected_ev is None or baseline_ev is None:
            raise TestSelectError(
                "RED_CHECK lacks a persisted COLLECT classification / pre-WRITE "
                "test.baseline_captured snapshot"
            )
        per_node = self._read_runtime_blob(collected_ev.payload.get("per_node_blob"))
        selected_by_layer: dict[str, list[str]] = {}
        for entry in per_node:
            if entry.get("class") == "r2":
                selected_by_layer.setdefault(entry.get("layer", ""), []).append(entry["node"])
        for nodes in selected_by_layer.values():
            nodes.sort()
        selected_all = sorted(
            node for nodes in selected_by_layer.values() for node in nodes
        )
        return baseline_ev.payload.get("baseline_id"), selected_by_layer, selected_all

    def _result_staging_path(self, command_id: str, section: str) -> Path:
        """Runtime-provided unique writable ``{result}`` test result path.

        Lives in system temp staging (per run/command/layer), never inside the
        repo tree, so it enters no tree identity and no write attribution."""
        base = Path(tempfile.gettempdir()) / "tracks-results" / self.run_id
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{command_id}-{section}.xml"

    def _has_persisted_unit_only_increment(self) -> bool:
        """Review-6: the empty-R2 hotfix bypass requires an explicit PERSISTED
        ``increment.declared(shield=empty, unit_rows nonempty)`` fact in this
        run's event stream -- never merely ``hotfix_issue is not None`` (a
        bare hotfix issue carries no increment to release)."""
        for ev in self.store.events(self.run_id):
            if ev.type != "increment.declared":
                continue
            payload = ev.payload or {}
            if payload.get("shield") == "empty" and payload.get("unit_rows"):
                return True
        return False

    def _emit_contract_error_red(self, cmd, state, reason: str) -> None:
        """Fail-closed contract_error channel for unusable results/inputs."""
        findings = [{"test_id": "*", "classification": "collection_error", "detail": reason}]
        log_ref = self._red_log_blob_ref({"error": reason})
        self._emit(
            "red.validated",
            {"status": "invalid", "findings": findings, "log_ref": log_ref},
            command_id=cmd.command_id,
        )
        self._emit(
            "verdict.failed",
            {
                "check": "contract_error",
                "target_stage": "M-DESIGN",
                "artifact_disposition": "rollback",
                "reason": reason,
                "evidence": findings,
                "log_ref": log_ref,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )
