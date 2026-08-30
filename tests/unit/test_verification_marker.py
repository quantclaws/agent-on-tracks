"""verification-only marker recognition must be a LEADING declaration.

Regression for run 01M0S0FQ v0.7 boundary: T-016's description said
"并 verification-only 重验 demo_host" (describing T-014's handling) while
Archer's emitted leading text was "实现 IF-FAILCLOSED-001 ...". A bare
substring match turned the real implementation task into verification-only,
so RED/GREEN looped on verify_task with Devon never dispatched.
"""

from tracks.kernel.m_impl import _is_verification_task
from tracks.kernel.machine import State


def _state(description: str) -> State:
    s = State()
    s.current_task_metadata = {"task_id": "T-XXX", "description": description}
    return s


def test_leading_verification_marker_recognized():
    assert _is_verification_task(_state("verification-only 验收闭口（plan_defect replan）：..."))


def test_bracket_leading_verification_marker_recognized():
    assert _is_verification_task(_state("【verification-only §1.0.3】验证"))


def test_mid_prose_mention_is_not_a_marker():
    # The actual v0.7 boundary regression: the phrase appears only in prose
    # describing another task's handling; this is a real RGR implementation
    # task and must NOT be classified verification-only.
    s = _state(
        "实现 IF-FAILCLOSED-001 双宿主九场景与 crash-recovery 演证，并原子拥有其"
        "公共入口接线、Phase 0 resume、终态 reach 前置与 executor 语言中立非回归"
        "（plan_defect replan ×4，#77 facade 判据）：(1) 本任务交付 "
        "tracks/executor/failclosed.py。T-014 保留 AC/IF provenance 并 "
        "verification-only 重验 demo_host。"
    )
    assert not _is_verification_task(s)


def test_standard_rgr_not_verification():
    assert not _is_verification_task(_state("实现 IF-ADAPTER-001 基础合同：..." ))


def test_empty_description_not_verification():
    assert not _is_verification_task(_state(""))
