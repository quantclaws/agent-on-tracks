"""FR-0277-02 sync-product preparation mixin (extracted from verify_park).

Shared by the normal M-VERIFY->M-RELEASE advance and the park evidence chain:
both call ``_park_preview`` -> ``_ensure_sync_products`` with the exact same
implementation, so a parked escalation cannot bypass the pre-decision product
preparation/verification.
"""

from __future__ import annotations

from tracks.executor import sync_product as _sync_product
from tracks.executor.release_gate import render_placeholders

_WHEN_ACTIVE_BRANCH = "when=active_release_branch"


class ExecSyncProductMixin:
    """Prepare + verify strict release-branch sync products before preview."""

    def _sync_targets(self, contract, journey, facts: dict) -> list[str]:
        """Declared conditional active-release merge targets for the journey.

        FR-0277-02 (doc B): only ``merge:<target>:when=active_release_branch``
        steps declared by the journey are sync candidates; no other merge
        step is turned into a sync.
        """
        decl = (getattr(contract, "operations", None) or {}).get(journey)
        if decl is None:
            return []
        targets: list[str] = []
        for step in getattr(decl, "steps", ()) or ():
            if not isinstance(step, str) or not step.endswith(
                f":{_WHEN_ACTIVE_BRANCH}"
            ):
                continue
            body = step[: -len(f":{_WHEN_ACTIVE_BRANCH}")]
            kind, _, target = body.partition(":")
            if kind != "merge" or not target:
                continue
            targets.append(render_placeholders(target, facts))
        return targets

    def _sync_verified_record(self, candidate_sha: str, target: str, baseline: str):
        return next(
            (
                record
                for record in _sync_product.verified_records(
                    list(self.store.events(self.run_id)), candidate_sha
                )
                if record.get("target") == target
                and record.get("baseline_sha") == baseline
            ),
            None,
        )

    def _emit_sync_prepared(self, cmd, candidate_sha: str, record: dict) -> None:
        events = list(self.store.events(self.run_id))
        barrier = _sync_product.stale_barrier(events, candidate_sha)
        seen = any(
            event.type == _sync_product.SYNC_PREPARED
            and int(getattr(event, "seq", 0) or 0) > barrier
            and (event.payload or {}).get("candidate_sha") == candidate_sha
            and ((event.payload or {}).get("product") or {}).get("product_sha")
            == record["product_sha"]
            for event in events
        )
        if seen:
            return
        self._emit(
            _sync_product.SYNC_PREPARED,
            {"candidate_sha": candidate_sha, "product": dict(record)},
            command_id=cmd.command_id,
        )

    def _sync_full_selection(self) -> tuple[list[str] | None, set[str]]:
        """The candidate's latest FULL selection + its approved waivers.

        FR-0277-02: P's complete-test ring re-executes exactly the selection
        M-VERIFY judged for C (minus the known-issue-waived nodes), never a
        blanket layer run that would resurrect already-waived Red tests.
        """
        selection = next(
            (
                event
                for event in reversed(list(self.store.events(self.run_id)))
                if event.type == "test.selected"
                and (event.payload or {}).get("scope") == "full"
            ),
            None,
        )
        if selection is None:
            return None, set()
        nodes = [
            str(node)
            for node in (selection.payload or {}).get("nodes") or []
            if str(node).strip()
        ]
        if not nodes:
            return None, set()
        return nodes, set(self._waived_nodes(nodes))

    def _sync_selection_digest(self, full_nodes, waived_nodes) -> str:
        return _sync_product.canonical_digest(
            {
                "nodes": sorted(str(node) for node in (full_nodes or ())),
                "waived": sorted(str(node) for node in (waived_nodes or ())),
            }
        )

    @staticmethod
    def _sync_failure_matches(
        payload: dict,
        candidate_sha: str,
        target: str,
        contract_digest: str,
        baseline: str | None,
        selection_digest: str,
    ) -> bool:
        if (
            payload.get("candidate_sha") != candidate_sha
            or payload.get("target") != target
            or payload.get("contract_digest") != contract_digest
            or payload.get("selection_digest") != selection_digest
        ):
            return False
        return baseline is None or payload.get("baseline_sha") == baseline

    def _sync_failure_seen(
        self,
        candidate_sha: str,
        target: str,
        contract_digest: str,
        baseline: str | None,
        selection_digest: str,
    ) -> str | None:
        """The recorded failure reason for an unchanged preparation identity.

        Recovery is identity-keyed: a new candidate, a moved baseline, a
        revised contract or a changed FULL selection/waiver set (e.g. a
        newly registered Known Issue) retries; an unchanged failing identity
        keeps the chain blocked without re-running the verification battery.
        """
        events = list(self.store.events(self.run_id))
        barrier = _sync_product.stale_barrier(events, candidate_sha)
        for event in events:
            if event.type != _sync_product.SYNC_FAILED:
                continue
            if int(getattr(event, "seq", 0) or 0) <= barrier:
                continue
            payload = event.payload or {}
            if self._sync_failure_matches(
                payload,
                candidate_sha,
                target,
                contract_digest,
                baseline,
                selection_digest,
            ):
                return str(payload.get("reason") or "sync_preparation_failed")
        return None

    def _sync_failure(
        self, cmd, candidate_sha: str, target: str, reason: str, extra: dict | None = None
    ) -> str:
        """Fail-closed sync preparation stop: one audit pair per identity.

        Returns the reason so the caller keeps the chain blocked without a
        preview -- a failed product is never decided on, and no irreversible
        operation has run at this point.
        """
        payload = {"candidate_sha": candidate_sha, "target": target, "reason": reason}
        if extra:
            payload.update(extra)
        events = list(self.store.events(self.run_id))
        barrier = _sync_product.stale_barrier(events, candidate_sha)
        seen = any(
            event.type == _sync_product.SYNC_FAILED
            and int(getattr(event, "seq", 0) or 0) > barrier
            and (event.payload or {}) == payload
            for event in events
        )
        if not seen:
            self._emit(_sync_product.SYNC_FAILED, payload, command_id=cmd.command_id)
            self._emit(
                "attention.required",
                {
                    "area": "sync_product",
                    "reason": reason,
                    "stage": "M-RELEASE",
                    "candidate_sha": candidate_sha,
                    "detail": f"sync product preparation for {target!r} failed",
                    "next": (
                        "resolve the merge/verification gap or fix the remote "
                        "baseline; trac run retries in place"
                    ),
                },
                command_id=cmd.command_id,
            )
        return reason

    def _ensure_sync_products(
        self, cmd, candidate_sha: str, contract, contract_digest, journey, facts, state
    ) -> str | None:
        """Prepare + verify sync products before the M-RELEASE preview.

        FR-0277-02: a diverged conditional active-release target gets a
        deterministic merge product P built from (B, C), verified in a real
        isolated checkout against the host-declared gates/test ladder/security
        and required-CI rings. An already-verified record for the same
        (candidate, target, baseline) is reused verbatim (crash recovery
        keeps the same P). Returns None when the chain may continue to the
        preview, or the blocking reason (no preview, no decision, no
        irreversible operation).
        """
        targets = self._sync_targets(contract, journey, facts)
        if not targets:
            return None
        version = getattr(state, "version", None) or next(
            (
                event.version
                for event in reversed(list(self.store.events(self.run_id)))
                if event.version
            ),
            "",
        )
        full_nodes, waived_nodes = self._sync_full_selection()
        selection_digest = self._sync_selection_digest(full_nodes, waived_nodes)
        for target in targets:
            reason = self._prepare_sync_target(
                cmd,
                candidate_sha,
                target,
                contract,
                contract_digest,
                facts,
                version,
                selection_digest,
                full_nodes,
                waived_nodes,
            )
            if reason is not None:
                return reason
        return None

    def _prepare_sync_target(  # pylint: disable=too-many-locals
        self,
        cmd,
        candidate_sha: str,
        target: str,
        contract,
        contract_digest,
        facts,
        version,
        selection_digest,
        full_nodes,
        waived_nodes,
    ) -> str | None:
        """One conditional target: ``None`` = ready/skip, reason = blocked."""
        failure_extras = {
            "contract_digest": contract_digest,
            "selection_digest": selection_digest,
        }
        baseline, error = _sync_product.remote_branch_tip(self.repo, target)
        if error is not None:
            return self._sync_failure(
                cmd, candidate_sha, target, error, dict(failure_extras)
            )
        if baseline is None:
            return None  # branch vanished: the plan builder owns silent skip
        need, error = _sync_product.sync_need(self.repo, baseline, candidate_sha)
        if error is not None:
            return self._sync_failure(
                cmd,
                candidate_sha,
                target,
                error,
                {**failure_extras, "baseline_sha": baseline},
            )
        if need != "product":
            return None  # contained or fast-forward: no product
        if self._sync_verified_record(candidate_sha, target, baseline):
            return None
        recorded = self._sync_failure_seen(
            candidate_sha, target, contract_digest, baseline, selection_digest
        )
        if recorded is not None:
            return recorded
        record, error = _sync_product.build_product(
            self.repo, target, baseline, candidate_sha
        )
        if error is not None:
            return self._sync_failure(
                cmd,
                candidate_sha,
                target,
                error,
                {**failure_extras, "baseline_sha": baseline},
            )
        if full_nodes is None:
            return self._sync_failure(
                cmd,
                candidate_sha,
                target,
                "sync_tests_unselected",
                {**failure_extras, "baseline_sha": baseline},
            )
        self._emit_sync_prepared(cmd, candidate_sha, record)
        evidence, details, error = _sync_product.verify_product(
            self.repo,
            contract,
            record,
            facts,
            version,
            full_nodes=full_nodes,
            waived_nodes=waived_nodes,
        )
        if error is not None:
            return self._sync_failure(
                cmd,
                candidate_sha,
                target,
                error,
                {
                    **failure_extras,
                    "baseline_sha": baseline,
                    "product_sha": record["product_sha"],
                    "evidence": details,
                },
            )
        record = {**record, "evidence_digests": evidence}
        self._emit(
            _sync_product.SYNC_VERIFIED,
            {
                "candidate_sha": candidate_sha,
                "target": target,
                "baseline_sha": baseline,
                "contract_digest": contract_digest,
                "product": record,
                "evidence": details,
            },
            command_id=cmd.command_id,
        )
        return None

