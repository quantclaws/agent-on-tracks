# B91 regression nodes — structural classification advisory (Shield → Runtime/M-DESIGN)

## Finding

Runtime verdict `test_defect` (attempt 1) classifies these two nodes as
`unexpected_pass` under D-41 red-execution semantics:

- `tests/integration/test_rgr_contract.py::test_rgr_lineage_binds_latest_checkpoint_after_shield_fix_overwrite`
- `tests/integration/test_rgr_contract.py::test_rgr_lineage_no_checkpoint_fail_closed`

The defect is **structural, not an assertion fault**. Both nodes were added by
this project's own test-plan §10 row 6 as 【R 修订（B91）】"追加 lineage 锚点解析序
integration 回归 … 耐久合同化：… Runtime 侧 unit 回归已随 f638075 落盘，本行补
Shield 侧公开出口覆盖". The behaviour they pin entered the tree in commit
f638075 ("fix(executor): B91 G lineage binds the R ref family"), i.e. before
the v0.7 M-DESIGN freeze that promoted the same semantics into interfaces.md
§5 IF-IMPL-004's v0.7 extension. The delta-declaration classifier therefore
marks them R2/T-DELTA (`delta_r2=2`, `basis=delta-declaration`), and D-41
requires legal Red now — but no black-box assertion over unchanged public
exits can fail while the implementation honours the frozen contract clause by
clause.

## Evidence produced this round

1. **Clause probes at HEAD c3c407d** (public exits only: store dispatch,
   `red.checkpointed`/`green.committed` payloads, git refs/trailers,
   `verify_lineage`): step‑1 exact-match authority (including a non-latest
   member), step‑2 mismatch fallback to seq-latest, step‑3 empty-family
   fail-closed, dedup visible exit — all honoured. Matrix recorded in
   `manifest.json#probe_evidence_at_HEAD_c3c407d`.
2. **Discrimination proofs**: each counterexample kills its bound test
   (CE‑1/CE‑2 re-verified against the strengthened tests; new CE‑3 pins
   step‑1 authority). Kill records live in `tests/counterexamples/v0.7/`.
3. **Strengthened hit-surface**: both named tests now assert the complete
   closed resolution order of IF-IMPL-004 v0.7 instead of only its step‑2/3
   tails; function names, file paths, and `AC-FR0120-02@v0.5` markers are
   unchanged per §10 row 6.

## What cannot be done from M-TEST

- Manufacturing Red by weakening/inverting assertions, skipping, or asserting
  falsehoods = test-plan §1.3 cheating patterns #1/#2/#10.
- Reverting f638075 or stubbing the region = product-code/interface-stub
  writes, outside Shield authority.
- Re-classifying the pair is a Runtime/M-DESIGN decision on the node-lane
  semantics, not a test-body edit.

## Requested routing (M-DESIGN decision space; Runtime executes)

1. **Recommended**: declare the two §10-row-6 nodes as version-inherited
   regression anchors for v0.7 (consistent with their mandated
   `AC-FR0120-02@v0.5` markers and the row's own "耐久合同化/回归" wording), so
   they execute in R1/T-HIST lanes where passing is legitimate; D-41's
   delta set for this stage becomes empty rather than forced-red. Alternatively
   fold the semantic registration into a future delta-declaration field so
   "contractualization of pre-baseline behaviour" stops colliding with
   new-node Red obligations.
2. **Not recommended**: keep R2 status — that requires Archer to introduce a
   stub regime over `executor/rgr.py` / `executor/m_impl_runtime.py` for an
   already-implemented, runtime-hotfixed contract, inverting the "Runtime 侧
   unit 回归已随 f638075 落盘" premise recorded in this repo's own test-plan.

Until one option is applied, repeated red-runs of exactly these two nodes will
reproduce `unexpected_pass` deterministically; it is not flakiness.
