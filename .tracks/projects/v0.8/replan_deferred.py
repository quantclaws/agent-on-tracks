#!/usr/bin/env python3
"""
Mechanical replan: 可证性分类重写 tasks.json
- 每个锚点要求的模块集 = ast_modules ∪ dynamic_modules (advisory) 过滤到落在某个任务 scope 文件内的模块
- 若 要求集 ⊆ owner 任务的 scope 传递闭包 (自身 scope ∪ depends_on 传递闭包) → 保留 acceptance_refs
- 否则 → 移入 deferred_refs (运行时豁免，最终由 T-INT 收口)
- 新增 T-INT 集成任务
- 自检：全图并集守恒、无环、T-INT 唯一汇点最后批次、每任务两集合不交
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# 路径定位
# ---------------------------------------------------------------------------
SCRIPT_PATH = Path(__file__).resolve()
# .tracks/projects/v0.8/replan_deferred.py -> repo root is parents[3]
try:
    REPO_ROOT = SCRIPT_PATH.parents[3]
    if not (REPO_ROOT / "tracks" / "__init__.py").exists():
        REPO_ROOT = Path.cwd().resolve()
except Exception:
    REPO_ROOT = Path.cwd().resolve()

TASKS_JSON = REPO_ROOT / ".tracks" / "projects" / "v0.8" / "tasks.json"
ANCHOR_SURFACE = REPO_ROOT / ".tracks" / "projects" / "v0.8" / "anchor-surface.json"
TASKS_MD = REPO_ROOT / ".tracks" / "projects" / "v0.8" / "tasks.md"

# ---------------------------------------------------------------------------
# 模块→路径映射（与 tracks/executor/taskgraph._module_to_path 一致）
# ---------------------------------------------------------------------------
def module_to_path(mod: str, repo: Path | None = None) -> str:
    base = mod.replace(".", "/") + ".py"
    candidate_dir = mod.replace(".", "/")
    root = Path(repo).resolve() if repo is not None else REPO_ROOT
    init_path = root / candidate_dir / "__init__.py"
    if init_path.is_file():
        return Path(candidate_dir).as_posix()
    return Path(base).as_posix()

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def parse_scope_paths(scope_boundary: str):
    if not scope_boundary or not scope_boundary.strip():
        return []
    parts = [p.strip() for p in scope_boundary.replace("\n", ",").split(",") if p.strip()]
    # 规范化：\ -> /, 去除尾 /
    normed = []
    for p in parts:
        pp = p.replace("\\", "/").rstrip("/")
        if pp:
            normed.append(pp)
    return normed

def transitive_closure_scopes(task_id: str, task_by_id: dict, memo: dict):
    """返回 task_id 的 depends_on 传递闭包内所有任务的 scope 并集（不含自身）"""
    if task_id in memo:
        return memo[task_id]
    visited = set()
    stack = list(task_by_id[task_id].get("depends_on", []))
    scopes = set()
    while stack:
        cur = stack.pop()
        if cur == "-" or cur in visited:
            continue
        visited.add(cur)
        t = task_by_id.get(cur)
        if t is None:
            continue
        for p in parse_scope_paths(t.get("scope_boundary", "")):
            scopes.add(p)
        # 传递
        for dep in t.get("depends_on", []):
            if dep not in visited:
                stack.append(dep)
    memo[task_id] = scopes
    return scopes

def find_cycle(tasks):
    """检测 depends_on 环，返回环描述或 None"""
    ids = {t["task_id"] for t in tasks}
    adj = {t["task_id"]: [d for d in t.get("depends_on", []) if d != "-" and d in ids] for t in tasks}
    color = {tid: 0 for tid in adj}  # 0 white,1 gray,2 black
    parent = {}
    def dfs(start):
        stack = [(start, 0)]
        color[start] = 1
        while stack:
            node, idx = stack[-1]
            neigh = adj.get(node, [])
            if idx < len(neigh):
                stack[-1] = (node, idx+1)
                child = neigh[idx]
                if color.get(child, 0) == 1:
                    # 找到环，提取
                    path = [node]
                    cur = node
                    while cur != child and cur in parent:
                        cur = parent[cur]
                        path.append(cur)
                    path.reverse()
                    return "->".join(path + [path[0]])
                if color.get(child, 0) == 0:
                    color[child] = 1
                    parent[child] = node
                    stack.append((child,0))
            else:
                color[node] = 2
                stack.pop()
        return None
    for tid in sorted(adj):
        if color[tid] != 0:
            continue
        c = dfs(tid)
        if c:
            return c
    return None

def main():
    print("="*80)
    print("Mechanical Replan: 可证性分类")
    print(f"REPO_ROOT: {REPO_ROOT}")
    print(f"TASKS_JSON: {TASKS_JSON}")
    print(f"ANCHOR_SURFACE: {ANCHOR_SURFACE}")
    print("="*80)

    # 读 anchor-surface 实际结构
    if not ANCHOR_SURFACE.exists():
        print(f"[FATAL] anchor-surface not found: {ANCHOR_SURFACE}", file=sys.stderr)
        sys.exit(2)
    with open(ANCHOR_SURFACE, "r", encoding="utf-8") as f:
        surface_data = json.load(f)
    # 结构中含 anchors 映射：锚点 -> ast_modules / dynamic_modules / modules / outcome
    anchors_map = surface_data.get("anchors")
    if not isinstance(anchors_map, dict):
        print(f"[FATAL] anchor-surface.json missing 'anchors' dict", file=sys.stderr)
        sys.exit(2)
    print(f"anchor-surface: total anchors = {len(anchors_map)}")
    # 检查每个锚点条目的实际键
    sample_keys = set()
    for v in list(anchors_map.values())[:1]:
        if isinstance(v, dict):
            sample_keys = set(v.keys())
    print(f"anchor entry keys (sample): {sample_keys}")
    # 校验是否含 ast_modules / dynamic_modules
    has_ast = any(isinstance(v, dict) and "ast_modules" in v for v in anchors_map.values())
    has_dyn = any(isinstance(v, dict) and "dynamic_modules" in v for v in anchors_map.values())
    print(f"has ast_modules={has_ast}, has dynamic_modules={has_dyn}")

    # 读 tasks.json 当前任务图
    if not TASKS_JSON.exists():
        print(f"[FATAL] tasks.json not found: {TASKS_JSON}", file=sys.stderr)
        sys.exit(2)
    with open(TASKS_JSON, "r", encoding="utf-8") as f:
        tasks_data = json.load(f)
    schema = tasks_data.get("schema", 1)
    tasks = tasks_data.get("tasks", [])
    if not isinstance(tasks, list):
        print("[FATAL] tasks.json 'tasks' not a list", file=sys.stderr)
        sys.exit(2)
    print(f"tasks.json: schema={schema}, tasks count={len(tasks)}")
    task_ids = [t.get("task_id") for t in tasks]
    print(f"task_ids: {task_ids}")

    # 构建 scope -> owner 映射
    scope_to_owner: dict[str, str] = {}
    task_by_id: dict[str, dict] = {}
    for t in tasks:
        tid = t.get("task_id")
        task_by_id[tid] = t
        for p in parse_scope_paths(t.get("scope_boundary", "")):
            # 若重复 scope，记录但后者覆盖（后续会检测 overlap，但此处仅用于 owner 判定，取最后）
            if p in scope_to_owner:
                print(f"[WARN] scope overlap detected during map: {p} owned by {scope_to_owner[p]} and {tid}")
            scope_to_owner[p] = tid
    print(f"scope_to_owner count={len(scope_to_owner)}")
    for p, owner in sorted(scope_to_owner.items()):
        print(f"  scope {p} -> {owner}")

    # 缓存所有模块的 path 映射
    all_mods = set()
    for entry in anchors_map.values():
        if not isinstance(entry, dict):
            continue
        for k in ("ast_modules", "dynamic_modules", "modules", "advisory_modules", "dynamic"):
            vals = entry.get(k)
            if isinstance(vals, list):
                for m in vals:
                    if isinstance(m, str):
                        all_mods.add(m)
    print(f"all distinct modules in surface: {len(all_mods)}")
    mod_to_path_cache: dict[str, str] = {}
    for m in all_mods:
        try:
            mod_to_path_cache[m] = module_to_path(m, REPO_ROOT)
        except Exception as e:
            mod_to_path_cache[m] = m.replace(".", "/") + ".py"
    # 计算落在某个任务 scope 文件内的模块子集（有效模块）
    in_scope_mods = {m for m, p in mod_to_path_cache.items() if p in scope_to_owner}
    print(f"in_scope_mods (mapped path in some task scope): {len(in_scope_mods)}")
    for m in sorted(in_scope_mods):
        print(f"  {m} -> {mod_to_path_cache[m]} (owner {scope_to_owner[mod_to_path_cache[m]]})")

    # 原始锚点全集（84）
    original_all_anchors = set()
    for t in tasks:
        for a in t.get("acceptance_refs", []):
            original_all_anchors.add(a)
    # 也检查是否原图使用 acceptance_refs 承载全部（schema 2）
    print(f"original acceptance_refs union count = {len(original_all_anchors)}")
    # 与 surface 对比
    surface_keys = set(anchors_map.keys())
    print(f"surface anchors count = {len(surface_keys)}")
    missing_in_surface = original_all_anchors - surface_keys
    extra_in_surface = surface_keys - original_all_anchors
    if missing_in_surface:
        print(f"[WARN] {len(missing_in_surface)} anchors in tasks but missing in surface: {missing_in_surface}")
    if extra_in_surface:
        print(f"[INFO] {len(extra_in_surface)} anchors in surface not in any task (should be 0 per current data): {list(extra_in_surface)[:5]}")
    # 按 spec 自检需全图 84
    if len(original_all_anchors) != 84:
        print(f"[WARN] original anchors count !=84, got {len(original_all_anchors)}")
    if len(surface_keys) != 84:
        print(f"[WARN] surface anchors count !=84, got {len(surface_keys)}")

    # 分类细则：对每任务的每个锚点，计算 required_module_set
    # required = ast_modules ∪ dynamic_modules (advisory) 过滤到 in_scope
    # 若 required ⊆ (own ∪ closure) => kept，否则 deferred
    # 同时记录越界模块清单

    # 预计算每任务的 provided scopes
    closure_memo: dict[str, set[str]] = {}
    task_provided: dict[str, set[str]] = {}
    task_own: dict[str, set[str]] = {}
    for tid in task_ids:
        own = set(parse_scope_paths(task_by_id[tid].get("scope_boundary", "")))
        task_own[tid] = own
        closure = transitive_closure_scopes(tid, task_by_id, closure_memo)
        provided = own | closure
        task_provided[tid] = provided

    # 分类结果存储
    new_tasks = []
    report_lines = []
    kept_total = 0
    deferred_total = 0
    # 用于自检：收集新图的并集
    new_all_acceptance = set()
    new_all_deferred = set()
    # 记录每个任务的拆分细节
    per_task_details = {}

    for t in tasks:
        tid = t["task_id"]
        own = task_own[tid]
        provided = task_provided[tid]
        closure = provided - own
        acceptance_refs = t.get("acceptance_refs", [])
        if not isinstance(acceptance_refs, list):
            acceptance_refs = []
        kept = []
        deferred = []
        deferred_details = []  # list of (anchor, out_of_bounds_list)
        for anchor in acceptance_refs:
            entry = anchors_map.get(anchor)
            if entry is None:
                # missing from surface -> 视为 deferred 还是 kept？按 fail-closed 视为 deferred 且告警
                print(f"[WARN] anchor {anchor} missing from surface, treat as deferred")
                deferred.append(anchor)
                deferred_details.append((anchor, [("UNKNOWN", "missing", "unknown")]))
                continue
            if not isinstance(entry, dict):
                entry = {}
            # 解析 ast_modules / dynamic_modules 的实际结构
            # 优先取 ast_modules, dynamic_modules；若不存在则回退到 modules
            ast_vals = entry.get("ast_modules")
            dyn_vals = entry.get("dynamic_modules")
            # 兼容部分旧数据可能使用 advisory 别名
            if ast_vals is None:
                #  legacy: modules 视为 ast
                legacy_mods = entry.get("modules")
                if isinstance(legacy_mods, list):
                    ast_vals = legacy_mods
                    dyn_vals = []
                else:
                    ast_vals = []
                    if dyn_vals is None:
                        dyn_vals = []
            if dyn_vals is None:
                # 尝试 advisory 键
                alt = entry.get("advisory_modules") or entry.get("advisory") or entry.get("dynamic") or []
                if isinstance(alt, list):
                    dyn_vals = alt
                else:
                    dyn_vals = []
            # 确保 list
            if not isinstance(ast_vals, list):
                ast_vals = []
            if not isinstance(dyn_vals, list):
                dyn_vals = []
            ast_set = {m for m in ast_vals if isinstance(m, str)}
            dyn_set = {m for m in dyn_vals if isinstance(m, str)}
            combined = ast_set | dyn_set
            # 过滤：只统计映射后落在某个任务 scope 文件内的模块
            required_mods = {m for m in combined if mod_to_path_cache.get(m) in scope_to_owner}
            required_paths = {mod_to_path_cache[m] for m in required_mods}
            # 判断是否 ⊆ provided
            if required_paths <= provided:
                kept.append(anchor)
            else:
                deferred.append(anchor)
                # 计算越界模块清单
                out = []
                for m in sorted(required_mods):
                    p = mod_to_path_cache[m]
                    if p not in provided:
                        owner = scope_to_owner.get(p, "unknown")
                        out.append((m, p, owner))
                deferred_details.append((anchor, out))
        # 记录
        per_task_details[tid] = {
            "own": own,
            "closure": closure,
            "provided": provided,
            "kept": kept,
            "deferred": deferred,
            "deferred_details": deferred_details,
        }
        kept_total += len(kept)
        deferred_total += len(deferred)
        new_all_acceptance.update(kept)
        new_all_deferred.update(deferred)
        # 构造新任务：复制原任务，替换 acceptance_refs/deferred_refs
        new_t = dict(t)  # shallow copy
        # 保持原有顺序：更新 acceptance_refs, 新增 deferred_refs
        new_t["acceptance_refs"] = kept
        new_t["deferred_refs"] = deferred
        new_tasks.append(new_t)

    # 新增集成任务 T-INT
    other_ids = [t["task_id"] for t in tasks]
    tint_description = "最终集成收口：全图 deferred 锚点在此转硬门禁；跳过 RED，REFACTOR 与质量门禁不豁免"
    tint_task = {
        "task_id": "T-INT",
        "issue_number": 999,
        "description": tint_description,
        "ac_refs": [],
        "fr_refs": [],
        "if_ids": [],
        "unit_refs": [],
        "acceptance_refs": [],
        "deferred_refs": [],
        "scope_boundary": "",
        "depends_on": other_ids,
        "batch": "7",
        "parallel": False,
        "budget": 3,
        "integration": True
    }
    # 按 spec 补齐必要字段（若缺少）
    new_tasks.append(tint_task)
    print("\n" + "="*80)
    print("分类报告")
    print("="*80)
    for tid in task_ids + ["T-INT"]:
        if tid == "T-INT":
            print(f"\nT-INT: kept 0 deferred 0 (integration task, batch 7, depends_on {len(other_ids)} tasks)")
            continue
        det = per_task_details[tid]
        print(f"\n{tid}: kept={len(det['kept'])} deferred={len(det['deferred'])} (own {len(det['own'])} scopes, closure {len(det['closure'])} scopes)")
        print(f"  own: {sorted(det['own'])}")
        print(f"  closure(scopes): {sorted(det['closure'])}")
        print(f"  provided total: {sorted(det['provided'])}")
        if det['deferred']:
            print(f"  deferred anchors:")
            for anchor, out_mods in det['deferred_details']:
                print(f"    - {anchor}")
                if out_mods:
                    for mod, path, owner in out_mods:
                        print(f"        越界 {mod} -> {path} (owner {owner} not in closure)")
                else:
                    print(f"        (无越界模块但仍 deferred? 检查逻辑)")
        if det['kept']:
            print(f"  kept anchors:")
            for a in det['kept']:
                print(f"    - {a}")

    print("\n" + "-"*80)
    print(f"总计: kept {kept_total} + deferred {deferred_total} = {kept_total+deferred_total} (原 {len(original_all_anchors)})")
    print(f"新图 acceptance union {len(new_all_acceptance)}, deferred union {len(new_all_deferred)}")
    print("="*80)

    # -----------------------------------------------------------------------
    # 自检（脚本内置断言，失败非零退出）
    # -----------------------------------------------------------------------
    errors = []

    # 1. 全图 (acceptance ∪ deferred) 并集 == 原 84 锚点，无丢失、无重复、无新增
    new_union = new_all_acceptance | new_all_deferred
    if new_union != original_all_anchors:
        missing = original_all_anchors - new_union
        extra = new_union - original_all_anchors
        errors.append(f"并集不一致: missing {missing}, extra {extra}")
    if len(new_all_acceptance & new_all_deferred) != 0:
        overlap = new_all_acceptance & new_all_deferred
        errors.append(f"全局 acceptance 与 deferred 有交集: {overlap}")
    # 每任务内不交
    for tid in task_ids:
        det = per_task_details[tid]
        inter = set(det['kept']) & set(det['deferred'])
        if inter:
            errors.append(f"{tid} deferred ∩ acceptance != ∅: {inter}")
        # 检查无重复
        if len(det['kept']) != len(set(det['kept'])):
            errors.append(f"{tid} kept 有重复")
        if len(det['deferred']) != len(set(det['deferred'])):
            errors.append(f"{tid} deferred 有重复")
        # 检查并集等于原
        orig_set = set(task_by_id[tid].get("acceptance_refs", []))
        new_set = set(det['kept']) | set(det['deferred'])
        if orig_set != new_set:
            errors.append(f"{tid} 新并集 != 原: missing {orig_set-new_set} extra {new_set-orig_set}")

    # 2. depends_on 无环；T-INT 为唯一天然汇点的最后批次
    cycle = find_cycle(new_tasks)
    if cycle:
        errors.append(f"depends_on 有环: {cycle}")
    # 检查 T-INT batch==7 且为最大
    batches = []
    for t in new_tasks:
        b = t.get("batch")
        try:
            # 尝试按数字比较
            bi = int(str(b))
            batches.append((bi, str(b), t["task_id"]))
        except:
            batches.append((999, str(b), t["task_id"]))
    max_batch = max(bi for bi, _, _ in batches)
    tint_batch = next((b for t in new_tasks if t["task_id"]=="T-INT" for b in [t.get("batch")]), None)
    if str(tint_batch) != "7":
        errors.append(f"T-INT batch 应为 7, 实际 {tint_batch}")
    if max_batch != 7:
        errors.append(f"最大 batch 应为 7, 实际 {max_batch} (T-INT 应为最后批次)")
    # T-INT 为唯一汇点？检查是否所有其他任务都不是汇点依赖 T-INT，T-INT 依赖所有
    tint = next(t for t in new_tasks if t["task_id"]=="T-INT")
    if set(tint.get("depends_on", [])) != set(other_ids):
        errors.append(f"T-INT depends_on 应为全部其他任务 id, 实际 {tint.get('depends_on')} vs {other_ids}")
    # 检查 T-INT 是否被其他任务依赖（应无）
    dependents_of_tint = [t["task_id"] for t in new_tasks if "T-INT" in t.get("depends_on", [])]
    if dependents_of_tint:
        errors.append(f"T-INT 被其他任务依赖，不为汇点: {dependents_of_tint}")
    # 检查 batch 7 是否只有 T-INT
    batch7_tasks = [t["task_id"] for t in new_tasks if str(t.get("batch"))=="7"]
    if batch7_tasks != ["T-INT"]:
        errors.append(f"batch 7 应仅含 T-INT, 实际 {batch7_tasks}")

    # 3. deferred ∩ acceptance = ∅ 已检查

    if errors:
        print("\n[SELF-CHECK FAILED]")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\n[SELF-CHECK PASSED] 全图并集守恒、无环、T-INT 唯一汇点最后批次、每任务不交")

    # -----------------------------------------------------------------------
    # 产出新 tasks.json
    # -----------------------------------------------------------------------
    out_data = {
        "schema": schema,
        "tasks": new_tasks
    }
    # 保持与原文件一致的缩进与排序
    with open(TASKS_JSON, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n已写新 tasks.json -> {TASKS_JSON} (tasks {len(new_tasks)} )")

    # -----------------------------------------------------------------------
    # 任务细节重点打印（按要求返回）
    # -----------------------------------------------------------------------
    print("\n" + "="*80)
    print("重点任务拆分细节 (T-001/T-004/T-013/T-039)")
    print("="*80)
    for tid in ["T-001","T-004","T-013","T-039"]:
        if tid not in per_task_details:
            continue
        det = per_task_details[tid]
        print(f"\n--- {tid} ---")
        print(f"kept {len(det['kept'])} / deferred {len(det['deferred'])}")
        print(f"own scopes: {sorted(det['own'])}")
        print(f"closure scopes: {sorted(det['closure'])}")
        print(f"provided: {sorted(det['provided'])}")
        print("kept:")
        for a in det['kept']:
            print(f"  {a}")
        print("deferred:")
        for anchor, out in det['deferred_details']:
            print(f"  {anchor}")
            for mod, path, owner in out:
                print(f"    -> {mod} ({path}) owner={owner}")

    # -----------------------------------------------------------------------
    # 重新生成 tasks.md（若可）
    # -----------------------------------------------------------------------
    print("\n" + "="*80)
    print("tasks.md 重新生成检查")
    print("="*80)
    try:
        from tracks.executor.taskgraph import parse_tasks_json, render_tasks_md
        # 读新 tasks.json
        text = TASKS_JSON.read_text(encoding="utf-8")
        nodes, err = parse_tasks_json(text)
        if err:
            print(f"[WARN] parse_tasks_json 失败: {err} -> 保持 tasks.md 不动")
        else:
            rendered = render_tasks_md(nodes)
            # 对比现有 tasks.md
            if TASKS_MD.exists():
                existing = TASKS_MD.read_text(encoding="utf-8")
                if existing == rendered:
                    print(f"tasks.md 已与 render_tasks_md 一致，无需重写 ({TASKS_MD})")
                else:
                    TASKS_MD.write_text(rendered, encoding="utf-8")
                    print(f"已重新生成 tasks.md -> {TASKS_MD} (byte-identical to render_tasks_md)")
                    print(f"  原长度 {len(existing)} -> 新长度 {len(rendered)}")
            else:
                TASKS_MD.write_text(rendered, encoding="utf-8")
                print(f"tasks.md 不存在，已生成 -> {TASKS_MD}")
            # 额外校验：新 tasks.md 与渲染一致
            after = TASKS_MD.read_text(encoding="utf-8")
            if after != rendered:
                print("[ERROR] tasks.md 生成后不一致！", file=sys.stderr)
                sys.exit(1)
            print("tasks.md 生成方式：tracks/executor/taskgraph.render_tasks_md (与 m_impl_runtime._tasks_md 同源)")
    except Exception as e:
        print(f"[WARN] 无法导入 render_tasks_md 或生成失败: {e}")
        print("依据：未找到可用生成器，保持 tasks.md 不动")
        import traceback
        traceback.print_exc()

    print("\nDone.")
    sys.exit(0)

if __name__ == "__main__":
    main()
