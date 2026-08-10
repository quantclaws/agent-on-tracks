---
spec_id: SPEC-NNN
created: {YYYY-MM-DD}
status: draft
sha:
---

# {Feature Title} — Test Plan

<!-- Template guidance (read when generating the document; delete every guidance comment
     before delivery — the delivered document keeps content only):
  - 交付文档中出现的 blockquote 一律是 inline-discussion 讨论线程，模板指引不得残留为 blockquote。
    (In delivered docs every blockquote is an inline-discussion thread; template guidance
    must never survive as a blockquote.)
  - Conditional sections (§2.4 Test Data, §2.5 Installation & Isolation, §3 Ground Truth,
    §6 External Dependency Layered Testing) carry a When needed / Lifecycle guidance
    comment stating when the whole section may be omitted; omit heading and content when
    the condition does not apply, otherwise fill the section in.
-->

- **Related acceptance**: `.tracks/projects/{version}/acceptance.md`
- **Related interfaces**: `.tracks/projects/{version}/interfaces.md` (assertion basis — see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

This test plan only declares test methods that are **observable from outside the system**. Observable objects are limited to:

- Exposed external API / SDK interfaces
- Web service / CLI endpoints
- UI entry points (test **observed** behavior, not verify rendering)
- Structured log entries
- Persisted data files (JSON/parquet/...)
- Database tables

### 1.2. Non-observable Objects (tests do not directly depend on)

- Internal class hierarchies, scheduling state machines
- Intermediate data structures
- Implementation details (internal queues, registries, state variables)

**Observable contract**: Any internal state that acceptance validation needs must be provided by the implementation layer via dump/log/DB observation points. This is the responsibility of **interfaces.md** — if an AC needs to observe internal state, interfaces.md must have a corresponding outlet (see §6.5).

### 1.3. Cheating Patterns (CI enforced interception)

| #   | Cheating Pattern                | Typical Symptom                                |
| --- | -------------------------------- | ---------------------------------------------- |
| 1   | Change assertions to fit impl    | spec says "throw exception", test changes to "return False" |
| 2   | Use skip to evade validation     | skip/ignore (e.g. `pytest.skip`, `it.skip`) with "see e2e" but e2e is never written |
| 3   | Assertion degradation            | `assert issubclass(X, Exception)` instead of actually submitting and catching |
| 4   | try/except: pass                 | Exception path is swallowed                     |
| 5   | Over-mocking                     | Mock the framework core, testing mock behavior instead |
| 6   | Ground truth uses impl           | Expected value = impl output                   |
| 7   | Hardcoded expected values        | `assert result == 0.15` only because current impl outputs 0.15 |
| 8   | Trivial pass                     | `assert True` / `assert 1 == 1`                |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**
   - Each test function must have an R-1 marker comment line directly above its `def`: `# AC-FRXXXX-YY@v0.4 TRACKS-TRACE <optional description>` (long-format marker with `TRACKS-TRACE` token, FR-0080/FR-0130). The `TRACKS-TRACE` token is mandatory—without it the marker is invisible to the trace scanner. Multiple ACs bound to the same function get one marker line per AC.
   - CI scans `tests/`, verifying: each test references at least one AC; each AC is referenced by at least one test
   - Any check failure blocks merge
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），
     供 M-IMPL task 变绿子集划分。IF- 标识取值来自 interfaces.md §5 注册表。

2. **Assertion taboos** (CI static checks, violations block merge)
   - No `assert True` / `assert 1` / `assert <obj> is not None` as the sole assertion
   - No `try: ... except: pass` wrapping the code under test
   - No test skip/ignore (e.g. `pytest.skip` / `@pytest.mark.skip`, jest `it.skip`, Go `t.Skip`) without a GitHub issue link

3. **Test change classification** (required in PR description)
   - [ ] New AC (link to acceptance.md commit)
   - [ ] Spec change (link to spec commit)
   - [ ] Fix flake / environment issue (link to issue)

   **Prohibited category**: "impl behavior inconsistent with spec → change test". Reject directly during review.

4. **Testability fallback** (if an AC cannot be tested)
   - Do not mock internals to force it through
   - Register it as a framework-side testability requirement, requesting the implementation layer to add a public assembly point
   - Until resolved, mark the AC as "blocked by testability gap"

### 1.5. Test Division of Labor

- **Unit tests**: Written by the **implementer** (Devon, committed alongside impl in R-G-R). Unit tests are Devon's universal obligation for every implemented FR/NFR, enforced by the coverage gate (§5.1). They are **not** planned in §8 AC Coverage — Archer does not prescribe unit test functions or files.
- **Integration tests**: Written by the **test lead** (Shield) - covers module interface contracts defined in interfaces.md
- **E2E tests**: Written by the **test lead** (Shield) - covers user-facing happy paths only
- **Ground Truth (§3)**: Provided by an **independent developer** not involved in the implementation under test, or a **third-party library**
- **Review ownership**: All test changes are reviewed by the test lead; Ground Truth script changes require focused review of semantic consistency with the corresponding AC

---

## 2. Test Environment

### 2.1. Directory Layout (recommended)

```
tests/
├── unit/          # Unit tests; mirror source file directory structure
├── integration/   # Integration tests; verify module interface contracts
├── e2e/           # End-to-end scenario tests (happy path only)
├── assets/        # Offline, reproducible test data
└── ground_truth/  # Optional: pure/reference implementation; must not import the system under test (see §3.2)
```

<!-- Template guidance (delete before delivery):
  - Unit tests and E2E use different data (separated by time/scenario) to prevent overfitting.
  - E2E must not mock internal framework implementation (if mocking is necessary, the AC
    should be rewritten).
  - E2E must not depend on framework private APIs.
-->

### 2.2. Naming Conventions

- File: `test_<scenario>__<subscenario>.py`
- Function: `test_ac_<id>_<subscenario>`, e.g. `test_ac_020_08_empty_directory`

### 2.3. Execution

- **Offline**: Tests do not depend on network (data is pinned)
- **Execution order**: unit (fast) → integration → e2e (slow)
- **CI**: Run the full suite on every push
- **Isolation**: Integration and e2e use the project framework's marker/tag/select mechanism (e.g. pytest `@pytest.mark.integration` / `@pytest.mark.e2e`, jest `--testPathPattern`, `go test -run`, `cargo test --test`) to avoid mixing with unit tests

### 2.3.1. Test Execution Contract (`.tracks/project/project.toml`)

The host project test execution contract is declared in `.tracks/project/project.toml` (produced by Archer in M-DESIGN). M-TEST uses this contract to collect and run tests independently.

- **Integration**:
  - framework: {e.g. pytest}
  - paths: {e.g. ["tests/integration/"]}
  - collect: {e.g. `.venv/bin/python -m pytest --collect-only -q tests/integration/`}
  - run: {e.g. `.venv/bin/python -m pytest tests/integration/ --tb=short -q`}
  - cwd: {e.g. "."}
- **E2e** (if applicable):
  - framework: {e.g. pytest}
  - paths: {e.g. ["tests/e2e/"]}
  - collect: {e.g. `.venv/bin/python -m pytest --collect-only -q tests/e2e/`}
  - run: {e.g. `.venv/bin/python -m pytest tests/e2e/ --tb=short -q`}
  - cwd: {e.g. "."}

### 2.4. Test Data (project optional)

<!-- Template guidance (delete before delivery; conditional section):
  **When needed**: Required when the project has external data dependencies (historical data,
  third-party APIs, hardware input, etc.); pure algorithm / pure internal logic may omit
  this whole section.
-->

- **Source**: Built-in / remote API fetch / synthetic generation
- **Reproducible**: Each CI run should produce consistent results
- **Small data in-repo**: Recommend `tests/assets/`
- **Sensitive data**: Must not be committed; must be mocked or use synthetic data
- **Version snapshot** (if applicable): Use a manifest to record data version and generation time; CI validates the manifest

### 2.5. Installation & Isolation (project with build artifact)

<!-- Template guidance (delete before delivery; conditional section):
  **When needed**: Required when the project produces an installable artifact (CLI tool,
  library, service image, etc.). Pure script / pure in-repo tooling may omit this whole
  section.
  **Lifecycle**: This section is established in the first release's design and inherited by
  subsequent releases. It is only revised when the installation method itself changes (new
  platform, new package manager, new distribution channel). Unchanged does not mean
  untested — CI still runs the installation step on every E2E pass; it means the
  specification here is not re-authored.
-->

- **Installation method**: Must be identical to the end user's (e.g. `pip install dist/*.whl`, `npm install -g <pkg>.tgz`, `docker load < image.tar`). E2E must not import from the source tree or use editable/development installs.
- **Install target**: An isolated prefix per CI run / test session (e.g. a fresh venv, `$TMPDIR/<run-id>/prefix`, a throwaway container). Must not pollute the system environment or the source tree.
- **Working directory**: E2E runs from a directory **unrelated to the source tree** (e.g. `$TMPDIR/<run-id>/workdir`), simulating a user invoking the tool from an arbitrary location.
- **Initialization**: If the product requires an init/scaffold step (e.g. `trac init`, `git init`, `npm init`), E2E performs it in the isolated working directory as the first action, exactly as a new user would.
- **Verification**: After installation, E2E asserts the artifact is discoverable (on PATH / importable / service reachable) before proceeding to functional assertions.

---

## 3. Ground Truth Method

<!-- Template guidance (delete before delivery; conditional section):
  **When needed**: Required when the project has "algorithm correctness / rule correctness /
  computation result correctness" to verify (financial computation, rule engines, parsers,
  serialization, etc.); pure CRUD / UI rendering may omit this whole section.
-->

### 3.1. General Principle

**Do not hardcode expected values**. All ground truth is computed by **independent sources** at test runtime:

| Type                                                       | Independent Source                             |
| ---------------------------------------------------------- | ---------------------------------------------- |
| Algorithm correctness (intersection, trigger, matching, constraints) | **Manual calculation**: Write explicit small scripts |
| Evaluation metrics (Sharpe / win rate / annualized, etc.) | **Third-party library** (the project's chosen metrics library) |
| Simple rules (is it a holiday, does it satisfy a condition) | **The data itself**: Test dataset as the single source of truth |

Key design: ground truth is a **recomputable script**, not a documented fixed value. At test runtime, the same data + ground truth script is called and compared with the framework output.

### 3.2. Ground Truth Isolation (mandatory rule)

To prevent circular validation where "expected value = output of the implementation under test" (§1.3 cheating pattern #6), ground truth scripts must satisfy:

1. **Code location**: All ground truth scripts are stored in the `tests/ground_truth/` directory (shared by unit/e2e)
2. **Import taboo**: `tests/ground_truth/**/*.py` **must not** `import {project}.*` (including submodules); CI static check blocks merge on violation
3. **Allowed dependencies**: Only standard library + test data files + agreed-upon third-party libraries (algorithm reference implementations)
4. **Data access**: Read data files directly from `tests/assets/*/fixtures/data/`, **not** through the framework's SDK
5. **Review ownership**: Ground truth script changes must be reviewed by the test lead; semantic consistency with the corresponding AC is the review focus

---

## 4. Test Scope

This test plan covers all requirements in spec.md in the same directory (and any other sibling spec documents it imports) where Valid / Testable / Decided are all green.

| Valid | Testable | Decided |
| ----- | -------- | ------- |
| ✅    | ✅       | ✅      |

---

## 5. Acceptance Criteria

1. Unit test coverage ≥95% (specific tooling depends on project language)
2. Every cross-module interface contract defined in interfaces.md has at least one integration test (happy + key error/edge paths).
   A **cross-module interface** = an interfaces.md entry whose `modules` column lists 2+ modules (Archer marks this from architecture.md's module boundaries). Shield reads this column as a checklist; it does not infer module boundaries.
3. User scenarios in Stories and Spec are fully covered by e2e happy paths and pass
4. All FRs have corresponding test coverage (AC reference closure)
5. If §6 external dependency layered testing is enabled: L1/L2 pass by default in CI; L3 is runnable in the corresponding environment

---

## 6. External Dependency Layered Testing (project optional)

<!-- Template guidance (delete before delivery; conditional section):
  **When needed**: Required when the project has external dependencies (databases,
  third-party APIs, hardware, real time, remote services, etc.); pure internal logic may
  omit this whole section. This section is an extension of §1's black-box stance: when the
  system interacts with the external world, how tests handle these external dependencies.
-->

### 6.1. Three Unavoidable Constraints

| #   | Constraint                                | Consequence                                                |
| --- | ----------------------------------------- | ----------------------------------------------------------- |
| C1  | Test environment cannot connect to production dependencies | CI / cross-platform dev machines cannot run production paths |
| C2  | Cannot wait for real time                 | Cross-day / cross-week strategy cycle tests are infeasible  |
| C3  | Cannot mock framework internals           | Replacing/patching bypasses the behavior under test, violating the black-box stance |

<!-- Template guidance (delete before delivery):
  §2's offline data environment alone cannot make paths with external dependencies run —
  this section exists for that purpose.
-->

### 6.2. Stance: Controllable vs Mock

- **Replace external dependencies** (controllable): Wall clock, external services, remote APIs, hardware — these are **external dependencies** of the framework under test and can be replaced with deterministic stand-ins
- **Cannot mock internal implementation**: The framework's own matching, scheduling, rules — these are **the object under test** and must not be mocked

**Boundary iron rule**: Under no circumstances may you replace or bypass the framework's own critical implementation to "make the test pass". If a test finds it must bypass to pass, it means the AC's observability design is wrong; revise interfaces/acceptance instead of patching the test side.

### 6.3. Three-Layer Test Pyramid

Divided into three layers by fidelity/cost/speed. The ACs covered by each layer do not overlap; test markers strictly distinguish run timing.

| Layer | Name                | Time          | Speed  | Coverage               | Default Run       |
| ----- | ------------------- | ------------- | ------ | ---------------------- | ----------------- |
| L1    | Deterministic sim   | Virtual clock | Seconds | Most business ACs     | ✅ CI default     |
| L2    | Contract sim        | Virtual clock | Seconds | Protocol/interface contract ACs | ✅ CI default |
| L3    | Real env smoke      | Real calendar | Real   | Cross-mode run / minimal smoke | ❌ nightly/manual |

- **L1 Deterministic sim**: Replace "time advancement" and "external data sources" with deterministic stand-ins, running through several days of business cycles
- **L2 Contract sim**: Start a stand-in service that follows the same protocol (database/gateway/external API); the framework interacts with the stand-in
- **L3 Real env smoke**: Real calendar + real dependencies, single round-trip smoke (≤1 transaction); deselected by default, only runs in environments with real dependencies

Any L3 test **must** be tagged with the corresponding marker (replacing the skip in §1.4); it must not evade L3 with a skip that has no issue link.

### 6.4. Responsibility Contract of Test Infrastructure

<!-- Template guidance (delete before delivery):
  Defines the responsibilities + external observable boundaries of stand-in components, for
  test engineers to implement. Does not prescribe internal implementation details (specific
  class names, method signatures are determined by the implementation layer).
-->

| Component        | Responsibility (external)              | Boundary (what it does not implement) |
| ---------------- | --------------------------------------- | -------------------------------------- |
| Virtual clock    | Given "current time" + can fast-forward | Does not implement cross-day settlement logic |
| Data replay source | Feed pinned data as time advances    | Does not implement business rules      |
| Stand-in service | Implement external protocol            | Does not implement the framework's business |
| Orchestrator     | Assemble stand-ins + advance time      | Does not execute business on behalf of the framework |

### 6.5. Assertion Basis — Closure with interfaces.md

Test assertions **may only** land on the external observable outlets defined in **interfaces.md**:

- Database table schema
- API response fields
- Structured log entries
- File schema

If a state needed by an AC has **no** corresponding observable outlet in interfaces.md, this is an observability gap; revise interfaces/acceptance to add the outlet, rather than snooping internal state in the test.

---

## 7. CI Gate

<!-- Template guidance (delete before delivery):
  Name here the host-repo CI required checks designed in architecture.md's CI
  contract, together with their validation items and failure semantics. Only
  cite commands/tools that exist; anything to be created first must be
  explicitly marked as to-be-implemented (listed as a Devon foundation task).
-->

- **Required check**: {host CI check names from architecture.md's CI contract}
- **Validation items**:
  - AC reference closure (each AC ≥1 test, each test ≥1 AC)
  - Anti-pattern static scan (see §1.3)
  - Coverage ≥95%
  - §3.2 ground truth isolation (if ground_truth/ is enabled)

---

## 8. AC Coverage

<!-- Template guidance (delete before delivery; delivered docs keep only the table):
  This table (D-28 / M-DESIGN→M-TEST contract) is the ONLY machine-readable
  source of AC test tasks. Runtime validates it at the M-DESIGN EXIT gate and
  builds `assignment.test_tasks` from it before any Shield WRITE dispatch.
  Semantics:
  - Fixed header `AC id | layer | test | IF` (columns located by header cell;
    order is fixed: AC | layer | test | IF). The ZH canonical header
    `| AC | 层 | 测试 | IF- 归属 |` is also accepted (legacy v0.4).
  - Every acceptance AC (acceptance.md) must have a row here. Only ACs whose
    `layer` is `integration` or `e2e` are Shield tasks; a row with no
    integration/e2e layer is an error (if an AC has no observable outlet, the
    design must fix interfaces.md or the AC must be revised, not silently
    omitted).
  - `layer` ∈ {integration, e2e}; use `+`/`、`/`,` to list both if an AC is
    covered at both layers. Unit tests are NOT planned here — they are Devon's
    universal obligation in R-G-R, enforced by the coverage gate (§5.1).
  - Every row must carry one or more registered IF- identifiers
    (interfaces.md §5 registry, the FR-0140 green condition); a missing or
    unregistered IF- fails the M-DESIGN EXIT gate closed.
  - `test` is a human-readable test function/file name suggestion only; the
    blockquote is reserved for inline-discussion.
  Example row (replace with real coverage; do NOT keep example AC/IF ids):
  | AC id | layer | test | IF |
  |---|---|---|---|
  | AC-FR0000-01（示例） | integration | test_ac_fr0000_01 | IF-MTEST-001 |
  | AC-FR0000-02（示例） | integration + e2e | test_ac_fr0000_02_happy | IF-MTEST-001 |
-->

| AC id | layer | test | IF |
|---|---|---|---|
