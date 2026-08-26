# SHIELD_FIX seed data-premise kill verification (T-013, review rounds 8→9)

Scope: Shield-side host_repo v0.7 closure seed only
(`tests/integration/v07_closure_seed.py` + module-scoped application via
`tests/integration/conftest.py`). The bound integration anchors
(`tests/integration/test_trace_closure.py`,
`tests/integration/test_check_trace.py`) are FROZEN assets and were not
modified. This note records the counterexample self-check applied to the NEW
fixture data: each seeded premise must have discriminating power over the
IF-CLOSURE-001 §1i hard-error closed set through the public
`trac check trace --version v0.7 --json` outlet.

## KILL 1 — dropping the FULL pass premise

- Mutation of the FIXTURE (single-premise deviation, workspace untouched):
  build the seed chain without the `full.executed(serves_as_full_f)` event.
- Observed on public outlet: top-level `hard_errors` gains
  `full_pass_missing`; records degrade to `status=fail`.
- Verdict: **killed** — the FULL-pass premise is load-bearing; the anchor
  assertion (`closure` + closed-set errors) discriminates its absence.

## Topology premise: homogeneous patch/candidate identity

The frozen AC-FR0265-03 clause treats a record's ``mutation_evidence`` as
bound to the SAME candidate digest as ``candidate_digest``, so the fixture
declares ``patch_digest := candidate_digest`` per chain (§1g fixes the field
set, not synthetic values). Consequence: only *relative* divergence between
the two identities is discriminator-visible; a global constant swap is
contract-blind by construction (absolute digest pinning would violate
test-plan §3's no-hardcoding duty).

## KILL 2 — evidence bound to a different candidate identity

- Mutation of the FIXTURE (single-premise deviation): append ONE deviant
  `mutation.manifest` through the same public `Store.append` seam the seed
  itself uses, binding `patch_digest = sha256:ff…f` while the chain keeps
  the derived candidate (`candidate_digest` stays coherent elsewhere).
- Observed on public outlet: the harvest takes the last manifest per AC, so
  AC-FR0030-01's record violates the frozen AC-FR0265-03 binding predicate
  (`candidate_digest` neither equal to nor contained in `mutation_evidence`)
  — the frozen anchor's own assertion observes the mismatch and fails.
- Verdict: **killed** — relative same-candidate binding is observable at
  the contract outlet; no divergence can be seeded silently.

## Round-9 fixture tightening (this dispatch)

- `phase0.baseline_repaired` now carries the EXACT §1a row-1 field set: the
  previously added top-level `candidate_digest` key was inadmissible
  ("payload 未列字段不得作为通过证据") and never read by any stream folder.
  The listed digest slot `bound_node.digest` continues binding the repaired
  node to this build's candidate identity (homogeneous premise).
- The top-level binding keys on `mutation.manifest`
  (`ac/if_ref/candidate_digest/patch_digest`) are RETAINED deliberately:
  they are the live harvest seam the join consumes, and removing them
  regresses required anchor `test_candidate_digest_consistency` from
  non-empty records to `mutation_missing` (verified by probe variant runs).

## Red/green ledger (selected R2/T-DELTA run, current tree)

- `test_blocking_conditions_hard_errors`: **green** (serial and `-n 4
  --dist loadscope`).
- `test_candidate_digest_consistency`: **green** (non-empty per-AC records;
  homogeneous same-candidate premise).
- `test_closure_candidate_bound_pass`: **legal Red, product-owned residue**
  — the fail payload carries every evidence slot populated/consistent with
  hard_errors exactly `['foreign_candidate']`. Verified at seams: the pure
  join consumes `evidence["baseline_candidate"]`
  (tracks/checks/trace.py `_closure_errors`), while NO event folder in the
  entire `tracks/` tree ever writes `baseline_candidate` (single-occurrence
  rg evidence); `phase0.baseline_repaired` folding sets only
  `baseline_evidence`/`nodes`. Therefore `status=pass` is unreachable from
  ANY admissible .tracks event data until M-IMPL folds the listed baseline
  channel (`bound_node.digest`, published by this seed since round 8) into
  `entry["baseline_candidate"]`. Routing: product-side fix → Runtime/Devon
  (`tracks/cli/main.py` is T-013's allowed path). Seed premise delivered.
- `test_v06_trace_output_version_isolated_no_closure_leak`: **planned legal
  Red** per its own frozen docstring ("not wired -> legal Red anchor"); its
  repo is built in-body via setup_trace_repo without any v0.7 version dir,
  so the contracted fail-closed guard aborts before JSON emission. Not
  reachable by any fixture applied before the test body; unmodified.

## Reproduction (committed probe, throwaway repos)

```
PYTHONPATH=. .venv/bin/python tests/counterexamples/v0.7/shield_fix_seed_kill_probe_r9.py
# -> "KILL 1 ok: hard_errors = ['full_pass_missing', 'foreign_candidate'] ..."
# -> "KILL 2 ok: foreign-bound patch observed at outlet; ..."
# -> "ALL KILLS VERIFIED"
```

The probe builds throwaway host repos ONLY through public seams
(`seed_v05_approved_baseline` + `Store.append` + `cmd_check` stdout); no
product code, stub, or frozen test byte is modified.
