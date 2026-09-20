"""Build .tracks/projects/v0.9/tasks.json from the frozen design docs.

v6 (PLAN-002/003 + M5 budget): anchor sits on the task registering its AC;
anchor's ast-touched modules covered by scope ∪ deps-closure; T-006
(projections) is dep-free so the command cluster can dep it without a cycle;
every Devon card measured with the calibrated serializer (fixed ≈4470B +
87B×ac×anc + 90B×ac + 340B×anc + desc + manifest).
"""
import json
import re
from pathlib import Path

BASE = Path("/Users/openclaw/workspace/tracks/.tracks/projects/v0.9")
tp = (BASE / "test-plan.md").read_text(encoding="utf-8")
arch = (BASE / "architecture.md").read_text(encoding="utf-8")

ROW = {}
for line in tp.splitlines():
    if line.startswith("| AC-"):
        cells = [c.strip() for c in line.split("|")[1:-1]]
        ac, layer, tests, _ifs = cells
        nodes = [t.strip() for t in tests.split("+")]
        integ = [n for n in nodes if n.startswith("tests/integration/")]
        ROW[ac] = {"layer": layer, "integ": integ}

FR_UNION: dict[str, set[str]] = {}
for line in arch.splitlines():
    if line.startswith("- **") and "owner=" in line:
        fr = re.search(r"\*\*((?:N?FR)-\d{4})\*\*", line).group(1)
        FR_UNION.setdefault(fr, set()).update(set(re.findall(r"IF-[A-Z]+-\d{3}", line)))


def fr_of(ac: str) -> str:
    v = re.match(r"AC-((?:N?FR)\d{4})-\d{2}$", ac).group(1)
    return f"{v[:-4]}-{v[-4:]}"


ISSUE = {
    "FR-0288": 140, "FR-0289": 141, "FR-0290": 142, "FR-0291": 143,
    "FR-0292": 144, "FR-0293": 145, "FR-0294": 146, "FR-0295": 147,
    "FR-0296": 148, "FR-0297": 149, "FR-0298": 150, "FR-0299": 151,
    "FR-0300": 152, "FR-0301": 153, "FR-0302": 154, "FR-0303": 155,
    "FR-0304": 156, "FR-0305": 157, "FR-0306": 158, "FR-0307": 159,
    "FR-0308": 160, "FR-0309": 161, "FR-0310": 162, "FR-0311": 163,
    "FR-0312": 164, "FR-0313": 165, "FR-0314": 166, "FR-0315": 167,
    "NFR-0150": 168, "NFR-0151": 169, "NFR-0152": 170,
}


def node(ac: str) -> str:
    integ = ROW[ac]["integ"]
    assert len(integ) == 1, f"{ac} has {len(integ)} integration nodes"
    return integ[0]


# (tid, issue, batch, parallel, frs, if_ids, acs, anchors, deferred, scope, deps, desc)
DEFS = [
    ("T-006", 153, "1", False,
     ["FR-0302", "FR-0303", "FR-0304", "FR-0307"], ["IF-QUERY-001"],
     ["AC-FR0302-01", "AC-FR0302-02", "AC-FR0303-01", "AC-FR0303-02",
      "AC-FR0304-01", "AC-FR0304-02", "AC-FR0307-01", "AC-FR0307-02"],
     ["AC-FR0302-01", "AC-FR0302-02", "AC-FR0303-01", "AC-FR0303-02",
      "AC-FR0304-01", "AC-FR0304-02", "AC-FR0307-01", "AC-FR0307-02"],
     [],
     "tracks/server/projections.py",
     [],
     "verification-only 验收闭口（IF-QUERY-001）：projections.py 全部投影已交付在树"
     "（overview/detail/timeline/ac_chain/todos/config/executions/文档 revision+diff），"
     "8 条集成锚点实测全绿，无剩余实现面。验收=Runtime 直跑 acceptance_refs；"
     "已绿锚无合法 RED，不走 Devon RGR。"),
    ("T-001", 147, "2", False,
     ["FR-0291", "FR-0292", "FR-0293", "FR-0310", "NFR-0150"],
     ["IF-CMDSVC-001"],
     ["AC-FR0291-01", "AC-FR0291-02", "AC-FR0292-01", "AC-FR0292-02",
      "AC-FR0293-01", "AC-FR0293-02", "AC-FR0310-01", "AC-FR0310-02",
      "AC-NFR0150-01"],
     ["AC-FR0291-01", "AC-FR0291-02", "AC-FR0293-01",
      "AC-FR0293-02", "AC-FR0310-01", "AC-FR0310-02", "AC-NFR0150-01"],
      ["AC-FR0292-02"],
      "tracks/supervisor/db.py, tracks/supervisor/service.py",
      ["T-006"],
      "命令服务核心（IF-CMDSVC-001）：db.py 服务面八表+service_events；service.py "
      "CommandService 先持久化再执行、幂等去重/冲突拒绝、surface=cli 内联、pause 两段受理、"
      "run/hotfix 前检与澄清受理、批准/preview 绑定复用既有 gate（不改 CLI）。锚点=创建/澄清/"
      "暂停/延迟（0292-02 前检 deferred 至 T-INT；proj 经 deps）。"),
    ("T-002", 142, "3", True,
     ["FR-0289", "FR-0290"], ["IF-PROJ-001"],
     ["AC-FR0289-01", "AC-FR0289-02", "AC-FR0290-01", "AC-FR0290-02"],
     ["AC-FR0289-01", "AC-FR0289-02", "AC-FR0290-01", "AC-FR0290-02"], [],
     "tracks/supervisor/readiness.py",
     ["T-001"],
     "项目登记与就绪探测（IF-PROJ-001）：readiness.py 四类封闭探针逐项 ok+reason；未就绪阻断 "
     "create_run；登记归属展示经 T-001（deps 覆盖 service）。锚点=登记两件+就绪两件。"),
    ("T-003", 150, "3", True,
     ["FR-0299", "NFR-0152"], ["IF-WAIT-001"],
     ["AC-FR0299-01", "AC-FR0299-02", "AC-NFR0152-01"],
     ["AC-FR0299-01", "AC-FR0299-02", "AC-NFR0152-01"], [],
     "tracks/supervisor/waiting.py",
     ["T-001", "T-006"],
     "等待/quota/退避（IF-WAIT-001）：classify_wait 五类封闭集、enter/resolve_wait 持久化、"
     "next_probe 60s→900s 不热循环不编造倒计时。锚点=quota 两件+退避口径（NFR0152-01 同文件"
     "触及投影读回，deps 覆盖）。"),
    ("T-004", 166, "3", True,
     ["FR-0314"], ["IF-WEBAUTH-001", "IF-CMDGUARD-001"],
     ["AC-FR0314-01", "AC-FR0314-02", "AC-FR0314-03", "AC-FR0314-04"],
     ["AC-FR0314-02", "AC-FR0314-03", "AC-FR0314-04"],
     ["AC-FR0314-01"],
     "tracks/server/auth.py, tracks/server/guard.py, tracks/discuss/gate.py, tracks/discuss/cli.py",
     ["T-001"],
     "认证+防护（IF-WEBAUTH-001/IF-CMDGUARD-001）：auth.py scrypt 口令/会话 token/CSRF；"
     "guard.py 封闭 kind/schema 校验、actor_class 强制、realpath 范围防护；discuss/gate.py+cli.py "
     "裁决权属判定（service 侧惰性 import 复用）。0314-01 deferred（需 app 装配面）。"),
    ("T-005", 167, "4", True,
     ["FR-0315"], ["IF-SECRECY-001"],
     ["AC-FR0315-01", "AC-FR0315-02"],
     ["AC-FR0315-02"],
     ["AC-FR0315-01"],
     "tracks/server/redaction.py",
     ["T-004"],
     "SecretRedactor 全出口脱敏（IF-SECRECY-001）：API/SSE/HTML/日志统一受控引用；口令/token 不落盘。"
     "锚点=越权读取拒绝（guard+redaction，guard 经 deps 覆盖）。0315-01 全出口扫描 deferred 至 T-INT。"),
    ("T-007", 148, "3", True,
     ["FR-0296", "FR-0312"], ["IF-LEASE-001"],
     ["AC-FR0296-01", "AC-FR0296-02", "AC-FR0312-01", "AC-FR0312-02",
      "AC-FR0312-03"],
     ["AC-FR0296-01", "AC-FR0296-02", "AC-FR0312-01", "AC-FR0312-02",
      "AC-FR0312-03"],
     [],
     "tracks/supervisor/lease.py",
     ["T-001"],
     "租约/代次（IF-LEASE-001）：事务内单调代次、完成 CAS、旧代次结果 late_result 隔离。"
     "锚点=并发单派发+迟到隔离+0312 回拨三件（service 经 deps 覆盖）。"),
    ("T-015", 148, "3", True,
     ["FR-0296"], ["IF-SCHED-001"],
     ["AC-FR0296-03", "AC-FR0296-04"],
     ["AC-FR0296-03"],
     ["AC-FR0296-04"],
     "tracks/supervisor/scheduler.py",
     ["T-001"],
     "调度（IF-SCHED-001）：schedule 单行表单活动 run+有序队列；hotfix 显式可审计换队；"
     "pause 生效即抑制调度/唤醒。0296-04 换队全链 deferred 至 T-INT。"),
    ("T-008", 149, "3", True,
     ["FR-0297", "FR-0313"], ["IF-DRIVE-001"],
     ["AC-FR0297-01", "AC-FR0297-02", "AC-FR0313-01", "AC-FR0313-02",
      "AC-FR0313-03"],
     ["AC-FR0297-01", "AC-FR0297-02", "AC-FR0313-01", "AC-FR0313-02",
      "AC-FR0313-03"],
     [],
     "tracks/supervisor/worker.py, tracks/supervisor/worker_main.py, tracks/executor/drive.py",
     ["T-001"],
     "驱动引擎（IF-DRIVE-001）：drive.py drive_once 单步五态（CLI cmd_run 行为不变）+ worker 子进程"
     "认领/驱动/pause 边界生效。锚点=自动推进+人工门停住+abandon 三件（含不可恢复终态；"
     "db/service 经 deps 覆盖）。"),
    ("T-009", 152, "4", True,
     ["FR-0298", "FR-0300"], ["IF-RECOVER-001"],
     ["AC-FR0298-01", "AC-FR0298-02", "AC-FR0300-01", "AC-FR0300-02"],
     ["AC-FR0298-01", "AC-FR0298-02", "AC-FR0300-01", "AC-FR0300-02"],
     [],
     "tracks/supervisor/recover.py",
     ["T-001", "T-003"],
     "崩溃重启恢复（IF-RECOVER-001）：recover_on_startup——claimed 命令 requeue、waits 的 retry_at "
     "保留、租约过期授新代次、未完成 effects 衔接既有 WAL/幂等 reconcile。"),
    ("T-010", 146, "5", True,
     ["FR-0294"], ["IF-DOCREV-001"],
     ["AC-FR0294-01", "AC-FR0294-02", "AC-FR0294-03"],
     ["AC-FR0294-01", "AC-FR0294-02", "AC-FR0294-03"], [],
     "tracks/server/pages.py",
     ["T-001", "T-005", "T-006"],
     "页面壳与 Vditor 宿主（IF-DOCREV-001）：PAGES 封闭集渲染 + vditor_asset_tags 自源站引用"
     "（禁 CDN/禁可选引擎）+ revision 对比区。"),
    ("T-011", 158, "5", True,
     ["FR-0295", "FR-0301", "FR-0305", "NFR-0150"], ["IF-QUERY-001"],
     ["AC-FR0295-01", "AC-FR0295-02", "AC-FR0295-03", "AC-FR0295-04",
      "AC-FR0301-01", "AC-FR0301-02", "AC-FR0305-01", "AC-FR0305-02",
      "AC-NFR0150-02"],
     ["AC-FR0295-01", "AC-FR0295-02", "AC-FR0295-04", "AC-FR0301-01",
      "AC-FR0305-01", "AC-NFR0150-02"],
     ["AC-FR0295-03", "AC-FR0301-02", "AC-FR0305-02"],
     "tracks/server/api_query.py",
     ["T-001", "T-005", "T-006"],
     "只读查询路由（IF-QUERY-001）：api_query.py 全部 GET 端点（含命令状态查询），独立只读连接、"
     "响应过 redactor。锚点=命令读回三件（0295 持久化/去重/只读——查询交付面）+总览+时间线+查询 P95。"
     "0295-03/0301-02/0305-02 deferred（需真实 serve 栈/CLI 双通道）。"),
    ("T-012", 158, "5", True,
     ["FR-0306", "NFR-0152"], ["IF-STREAM-001"],
     ["AC-FR0306-01", "AC-FR0306-02", "AC-NFR0152-02"], [],
     ["AC-FR0306-01", "AC-FR0306-02", "AC-NFR0152-02"],
     "tracks/server/api_events.py",
     ["T-005", "T-006"],
     "事件订阅路由（IF-STREAM-001）：api_events.py SSE（id/event/data 帧、游标补读、wait=0 回退）。"
     "锚点全部需真实 serve 栈，B94 deferred 至 T-INT。"),
    ("T-013", 160, "5", True,
     ["FR-0308", "FR-0309", "FR-0311"], ["IF-WEBGATE-001"],
     ["AC-FR0308-01", "AC-FR0308-02", "AC-FR0309-01", "AC-FR0309-02",
      "AC-FR0309-03", "AC-FR0311-01", "AC-FR0311-02"],
     ["AC-FR0308-01", "AC-FR0308-02", "AC-FR0309-02", "AC-FR0309-03",
      "AC-FR0311-01", "AC-FR0311-02"],
     ["AC-FR0309-01"],
     "tracks/server/api_command.py",
     ["T-001", "T-004", "T-005"],
     "变更类 HTTP 路由（IF-WEBGATE-001）：api_command.py 全部变更端点（会话+CSRF+guard、"
     "Rejection→状态码映射、accept surface=http）。锚点=批准两件+三择一两件+受控重试两件"
     "（service 经 deps 覆盖）。0309-01 deferred（发布全链归 T-INT）。"),
    ("T-014", 140, "6", False,
     ["FR-0288", "NFR-0150"], ["IF-SERVE-001"],
     ["AC-FR0288-01", "AC-FR0288-02", "AC-FR0288-03", "AC-NFR0150-03"],
     ["AC-FR0288-02"],
     ["AC-FR0288-01", "AC-FR0288-03", "AC-NFR0150-03"],
     "tracks/server/app.py, tracks/cli/serve_cmd.py, tracks/cli/main.py",
     ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006", "T-007", "T-008",
      "T-009", "T-010", "T-011", "T-012", "T-013", "T-015"],
     "服务装配与入口（IF-SERVE-001）：app.py create_app 装配、serve_cmd.py cmd_serve（口令供给/"
     "recover 接入/uvicorn/优雅停止）、cli/main.py 注册 serve+USAGE 同步。锚点=重启恢复"
     "（ast 实测触 app+db+recover+service，本任务拥有 app/serve_cmd，其余经 deps 覆盖）。"
     "deferred：0288-01/03、NFR0150-03（真实服务进程栈）。"),
     ("T-INT", 998, "7", False,
      ["NFR-0151"], ["IF-MTEST-001", "IF-MTEST-002"],
      ["AC-NFR0151-01", "AC-NFR0151-02"],
      ["AC-NFR0151-01", "AC-NFR0151-02",
       # PRISM-PLAN-01 (round 5): the 13 deferred anchors get explicit
       # acceptance ownership here (surface entries + statically verifiable
       # GREEN feasibility via this task's full deps closure); the source
       # tasks keep them in deferred_refs as the B94 early-signal,
       # non-counting channel.
       "AC-FR0314-01", "AC-FR0315-01", "AC-FR0296-04",
       "AC-FR0295-03", "AC-FR0301-02", "AC-FR0305-02",
       "AC-FR0306-01", "AC-FR0306-02", "AC-NFR0152-02",
       "AC-FR0309-01", "AC-FR0292-02",
       "AC-FR0288-01", "AC-FR0288-03", "AC-NFR0150-03"], [],
      "tracks/server/app.py, tracks/server/api_command.py, tracks/server/api_query.py, "
      "tracks/server/api_events.py, tracks/supervisor/service.py, tracks/supervisor/worker.py, "
      "tracks/cli/serve_cmd.py, tracks/cli/main.py",
      ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006", "T-007", "T-008",
       "T-009", "T-010", "T-011", "T-012", "T-013", "T-014", "T-015"],
      "终局收口：全图 14 条跨域锚点声明为本任务 acceptance（deferred 留在源任务作早期信号、"
      "不计其 verdict，互斥不变），与可靠性八场景元扫描 + 守卫 registry/coverage 门槛继承 "
      "2 条共 16 条经真实 serve 子进程栈一次硬门禁转绿（跳过 RED，REFACTOR/质量门禁不豁免）。"),
]

tasks = []
for (tid, issue, batch, parallel, frs, ifs, acs, anchors, deferred, scope, deps, desc) in DEFS:
    t = {
        "task_id": tid,
        "issue_number": issue,
        "description": desc,
        "ac_refs": acs,
        "fr_refs": frs,
        "if_ids": ifs,
        "unit_refs": [],
        "acceptance_refs": [node(ac) for ac in anchors],
        "scope_boundary": scope,
        "depends_on": deps,
        "batch": batch,
        "parallel": parallel,
        "budget": 3,
        "deferred_refs": [node(ac) for ac in deferred],
    }
    if tid == "T-INT":
        t["integration"] = True
    tasks.append(t)

FIXED = 4470
BUDGET = 16384


def card_bytes(t: dict) -> int:
    slice_ = [
        {"ac_id": ac, "anchors": list(t["acceptance_refs"]), "if_ids": list(t["if_ids"])}
        for ac in t["ac_refs"]
    ]
    tt = len(json.dumps(slice_, ensure_ascii=False))
    payload = {
        "task_id": t["task_id"], "issue_number": t["issue_number"],
        "description": t["description"], "ac_refs": t["ac_refs"],
        "fr_refs": t["fr_refs"], "if_ids": t["if_ids"],
        "test_refs": [*t["unit_refs"], *t["acceptance_refs"]],
        "unit_refs": t["unit_refs"], "acceptance_refs": t["acceptance_refs"],
        "schema": 2, "scope_boundary": t["scope_boundary"],
        "depends_on": t["depends_on"], "batch": t["batch"],
        "parallel": t["parallel"], "budget": t["budget"],
        "deferred_refs": t["deferred_refs"], "integration": bool(t.get("integration")),
        "debt": [],
    }
    tp = len(json.dumps(payload, ensure_ascii=False))
    manifest = 1570 + 48 * len([x for x in t["scope_boundary"].split(",") if x.strip()])
    # acceptance_refs and test_refs appear as their own card sections too
    refs = 2 * len(json.dumps(t["acceptance_refs"], ensure_ascii=False))
    return FIXED + tt + tp + manifest + refs


errors = []
for t in tasks:
    size = card_bytes(t)
    flag = "OK " if size <= BUDGET else "OVER"
    print(f"{t['task_id']:>6} {flag} card≈{size}B (ac={len(t['ac_refs'])}, anchors={len(t['acceptance_refs'])})")
    if size > BUDGET:
        errors.append(f"{t['task_id']}: card {size}B over budget")

covered = {}
for t in tasks:
    for r in (*t["acceptance_refs"], *t["deferred_refs"]):
        covered.setdefault(r, []).append(t["task_id"])
for ac, row in ROW.items():
    if "integration" not in row["layer"]:
        continue
    for n in row["integ"]:
        if n not in covered:
            errors.append(f"{ac}: node {n} not covered")
for r, ts in covered.items():
    if len(ts) > 1:
        # B94 收口 mirror (PRISM-PLAN-01): a deferred anchor is legitimately
        # declared twice — once in its source task's deferred_refs (early
        # signal, non-counting) and once in the integration task's
        # acceptance_refs (final hard-gate ownership). Any other double
        # coverage stays an error.
        non_int = [t for t in ts if t != "T-INT"]
        integ = [t for t in ts if t == "T-INT"]
        mirror_ok = (
            len(integ) == 1
            and len(non_int) == 1
            and r in next(t for t in tasks if t["task_id"] == non_int[0])["deferred_refs"]
            and r in next(t for t in tasks if t["task_id"] == "T-INT")["acceptance_refs"]
        )
        if not mirror_ok:
            errors.append(f"node {r} covered by multiple tasks {ts}")
reg = {}
for t in tasks:
    for ac in t["ac_refs"]:
        reg.setdefault(ac, []).append(t["task_id"])
for ac in ROW:
    if ac not in reg:
        errors.append(f"{ac} not registered")
for ac, ts in reg.items():
    if len(ts) > 1:
        errors.append(f"{ac} registered twice: {ts}")
for t in tasks:
    for ac in t["ac_refs"]:
        union = FR_UNION[fr_of(ac)]
        for i in t["if_ids"]:
            if i not in union:
                errors.append(f"{t['task_id']}: {i} not in closure union of {ac}")
seen = {}
for t in tasks:
    if t.get("integration"):
        continue
    for pth in [x.strip() for x in t["scope_boundary"].split(",")]:
        if pth in seen:
            errors.append(f"scope overlap: {pth} in {seen[pth]} and {t['task_id']}")
        seen[pth] = t["task_id"]
repo = Path("/Users/openclaw/workspace/tracks")
for t in tasks:
    for pth in [x.strip() for x in t["scope_boundary"].split(",")]:
        if not (repo / pth).exists():
            errors.append(f"{t['task_id']}: scope path missing on disk: {pth}")
for t in tasks:
    inter = set(t["deferred_refs"]) & set(t["acceptance_refs"])
    if inter:
        errors.append(f"{t['task_id']}: deferred ∩ acceptance: {inter}")

node2ac = {n: ac for ac, row in ROW.items() for n in row["integ"]}
for t in tasks:
    if t.get("integration"):
        # The integration 收口 task owns the union of all deferred anchors by
        # design (B94); their ACs stay registered on the source tasks.
        continue
    reg_set = set(t["ac_refs"])
    for r in t["acceptance_refs"]:
        ac = node2ac.get(r)
        if ac and ac not in reg_set:
            errors.append(f"{t['task_id']}: anchor {r} ({ac}) not registered on this task (PLAN-003)")

surface = json.loads((BASE / "anchor-surface.json").read_text(encoding="utf-8"))
owner = {}
for t in tasks:
    if t.get("integration"):
        continue
    for pth in [x.strip() for x in t["scope_boundary"].split(",")]:
        owner[pth[:-3].replace("/", ".")] = t["task_id"]
closure_cache: dict[str, set[str]] = {}
DEFS_BY_ID = {d[0]: d for d in DEFS}


def close(tid: str) -> set[str]:
    if tid not in closure_cache:
        closure_cache[tid] = set()
        out = set(DEFS_BY_ID[tid][10])
        for dd in list(out):
            out |= close(dd)
        closure_cache[tid] = out
    return closure_cache[tid]


for t in tasks:
    if t.get("integration"):
        continue
    allowed = {t["task_id"]} | close(t["task_id"])
    for ref in t["acceptance_refs"]:
        entry = surface["anchors"].get(ref)
        if entry is None:
            continue
        for mod in entry.get("ast_modules", []):
            o = owner.get(mod)
            if o is not None and o not in allowed:
                errors.append(f"{t['task_id']}: anchor {ref} touches {mod} owned by {o}")

batches = {t["task_id"]: int(t["batch"]) for t in tasks}
for t in tasks:
    for d in t["depends_on"]:
        if batches[d] >= batches[t["task_id"]]:
            errors.append(f"{t['task_id']}: dep {d} not in earlier batch")

if errors:
    print("ERRORS:")
    for e in errors:
        print(" ", e)
    raise SystemExit(1)

out = BASE / "tasks.json"
out.write_text(json.dumps({"schema": 2, "tasks": tasks}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("written", out, "SELF-CHECK CLEAN")
