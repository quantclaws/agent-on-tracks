# Shield M-TEST 工作完成说明 + opencode.py 无限循环 Bug 修复报告

**日期**: 2026-08-14
**项目**: tracks v0.5
**阶段**: M-TEST (Shield WRITE)
**编写者**: Human (通过 TRAE agent)

---

## 一、Shield 工作回顾 (2026-08-14)

### 1.1 任务上下文

Shield 被指派执行 M-TEST WRITE 任务：根据 test-plan.md、interfaces.md 和 acceptance.md 编写 integration/e2e 测试。任务包含 120+ 条 AC（验收标准），覆盖 FR-0010 到 FR-0237 及 NFR 系列。

### 1.2 今日执行记录

| 轮次 | 时间 (UTC) | 子状态 | 步骤数 | 结果 |
|------|-----------|--------|--------|------|
| 1 | 10:07 | WRITE | ~80 | manifest 格式错误 (`artifact_manifest must be an object`) |
| 2 | 10:45 | WRITE | ~60 | manifest_malformed，重试 |
| 3 | 11:01 | WRITE | ~20 | 快速失败 |
| 4 | 11:18 | WRITE | 261 | **无限循环**：Step ~50 完成工作后，Step 51-261 重复输出 "assignment completed" |
| 5 | 12:10 | WRITE | ~80 | 再次尝试 |
| 6 | 12:19 | WRITE | ~100 | 再次尝试 |
| 7 | 12:50 | NO_DIFF_EXPLAIN | 66 | 解释为何 `git diff` 无输出 |

### 1.3 Shield 实际产出

**测试文件 (5 个，共 1220 行)**:

| 文件 | 行数 | 测试数 | 覆盖范围 |
|------|------|--------|----------|
| tests/integration/test_release_evidence.py | 419 | 10 | FR-0230~FR-0233/NFR-0080 release-evidence CLI |
| tests/integration/test_doc_comment_first.py | 260 | 9 | FR-0150 文档评论首次创建 |
| tests/integration/test_doc_comment_quarantine.py | 193 | 6 | FR-0150 文档评论隔离 |
| tests/e2e/test_doc_comment_journey.py | 82 | 1 | FR-0150 文档评论完整旅程 e2e |
| tests/e2e_live/test_m_impl_release_evidence.py | 266 | - | M-IMPL release-evidence live 证据 |

**Counterexample patch 文件 (7 个)**:
- doc_comment_adjudicate.patch
- doc_comment_classify.patch
- doc_comment_quarantine.patch
- doc_comment_resume.patch
- live_evidence_bind.patch
- live_evidence_write.patch
- release_evidence_checks.patch

### 1.4 测试质量评估

- 测试通过 `pytest --collect-only` 验证可收集，共 26 个测试用例
- 测试设计为 "legal Red"：断言落在公共 CLI 契约上，因接口桩未实现而预期失败
- 测试覆盖了 release-evidence、doc-comment 等核心 FR 路径
- `pytest.mark.integration` 未注册（宿主项目 pytest.ini 缺失 markers 声明），但不影响收集和执行

### 1.5 遗留问题

1. **测试文件未被 git 跟踪**：Shield 创建的文件处于 `git untracked` 状态，导致 `git diff` 无法检测到变化，Runtime 判定为 `NO_DIFF` 并进入 `NO_DIFF_EXPLAIN` → `NO_DIFF_REVIEW` 流程
2. **manifest 格式问题**：Shield 在早期尝试中输出的 `artifact_manifest` 不是对象类型，导致 Runtime 拒绝
3. **覆盖范围不完整**：120+ 条 AC 中仅覆盖了 FR-0150 和 FR-0230~FR-0233 两个簇，大量 AC 未被测试

---

## 二、无限循环 Bug 根因分析与修复

### 2.1 现象

Shield 在 attempt 4 (Step 261) 中，Step ~50 已完成所有工作并输出了包含有效 manifest 的 text event。但 opencode 进程未退出，继续注入空用户消息，导致 Shield 重复输出 "The assignment was completed successfully. The manifest was delivered as the WRITE outcome..."。这个循环持续了 200+ 步，直到被人工终止。

### 2.2 根本原因

**三层缺陷叠加**:

1. **opencode 1.18.1 `--auto` 模式不退出**:
   - Agent 产出 text-only response 后，opencode 不发送终止信号
   - 反而注入空用户消息 (`""`)，触发新一轮 agent 调用
   - Agent 识别到任务已完成，再次输出 "已完成"，但 opencode 依然不退出
   - 形成无限循环

2. **tracks Runtime 无超时机制**:
   - `OpencodeBackend._run()` 使用 `proc.communicate(input=...)` 阻塞等待进程退出
   - 没有设置 `timeout` 参数
   - 进程永远不退出 → `communicate()` 永远不返回 → Runtime 卡死

3. **清单提取逻辑只看最后一个 text event**:
   - `_extract_manifest()` 调用 `_final_text_event()` 只取最后一个 `type=text` 事件
   - 在无限循环中，最后一个 text event 是 "already completed" 散文，不是 manifest
   - 即使进程最终被杀掉，也无法从历史输出中恢复 manifest

### 2.3 修复方案

用户明确指示：**"正确的事是，一定要在 Agent 完成时，读到它的结果，然后退出 opencode 会话——不然也会有资源泄露"**

据此，实施了 **流式读取 + 实时 manifest 检测 + 进程终止** 方案：

#### 修改文件: `tracks/effects/opencode.py`

**改动 1: 新增 imports**

```python
import select
import threading
import time
```

**改动 2: 替换 `communicate()` 为流式读取**

原逻辑:
```python
stdout, stderr = proc.communicate(input=console_input, timeout=timeout)
```

新逻辑:
```python
# 1. 后台线程写 stdin（避免管道死锁）
stdin_writer = threading.Thread(target=_write_stdin, daemon=True)
stdin_writer.start()

# 2. 后台线程读 stderr（非 debug 模式）
stderr_reader = threading.Thread(target=_read_stderr, daemon=True)
stderr_reader.start()

# 3. 主线程流式读 stdout，逐行检测 manifest
while True:
    ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 5.0))
    if ready:
        line = proc.stdout.readline()
        if not line:
            break  # EOF — 进程已退出
        stdout_lines.append(line)
        # 检测是否是包含有效 manifest 的 text event
        event = json.loads(stripped)
        if event.get("type") == "text":
            payload, _ = _manifest_payload(event)
            include, _ = _manifest_include(payload)
            if include and commit_msg:
                manifest_found = True
                break  # 找到 manifest，立即跳出
    elif proc.poll() is not None:
        break  # 进程已退出

# 4. 找到 manifest 或超时 → 杀进程组
if manifest_found or proc.poll() is None:
    self._kill_group(proc.pid)
    proc.wait(timeout=10)
```

**改动 3: 改进 `_extract_manifest()` — 扫描所有 text event**

```python
# 旧逻辑：只看最后一个 text event
text_event = OpencodeBackend._final_text_event(proc)

# 新逻辑：逆序扫描所有 text event，找到第一个包含有效 manifest 的
text_events = OpencodeBackend._all_text_events(proc)
for text_event in reversed(text_events):
    payload, error = OpencodeBackend._manifest_payload(text_event)
    if error: continue
    include, error = OpencodeBackend._manifest_include(payload)
    if error: continue
    commit_msg = payload.get("suggested_commit_message")
    if isinstance(commit_msg, str) and commit_msg.strip():
        return {"include": include}, commit_msg, None
```

**改动 4: 新增 `_all_text_events()` 方法**

返回 stdout 中所有 `type=text` 事件的列表，按时间顺序排列。

**改动 5: 保留超时作为安全网**

```python
timeout = int(os.environ.get("TRAC_AGENT_TIMEOUT", "1800"))
deadline = time.monotonic() + timeout
```

**改动 6: 删除重复的 `_final_text_event()` 方法**

补丁应用过程中产生了重复方法定义，已清理。

### 2.4 修复验证

- `python3 -m py_compile tracks/effects/opencode.py` — 语法正确
- `grep` 验证关键方法存在: `_all_text_events`, `_extract_manifest`, `_kill_group`
- `git diff --stat` 确认改动范围: `tracks/effects/opencode.py | 173 +++++++++++++++++++-----`

### 2.5 未修复的项 (用户指示排除 E)

原方案中的 E 项（强化 agent 提示词，强调"输出 manifest 后不要再产出任何内容"）用户指示不修。理由：流式检测 + 进程终止已从根本层面解决问题，不需要依赖 agent 遵守提示词约束。

---

## 三、当前状态

| 维度 | 状态 |
|------|------|
| trac run | 运行中 (PID 44466) |
| 当前阶段 | M-TEST / NO_DIFF_REVIEW |
| Prism | 正在运行 (PID 59838)，评审 Shield 的 NO_DIFF_EXPLAIN |
| opencode.py 修复 | 已应用到工作区，未 commit |
| Shield 测试文件 | 已创建，git untracked |
| Model | litellm/deepseek-v4-flash (glm-5.2 配额耗尽，使用 fallback) |

---

## 四、请 Archer 评审的问题

1. **Shield 的测试设计质量**：26 个测试用例覆盖 2 个 FR 簇 (FR-0150, FR-0230~233)，120+ 条 AC 中大部分未覆盖。这是否满足 M-TEST WRITE 的验收标准？还是需要重新派发 Shield 补全？

2. **opencode.py 流式修复的正确性**：流式读取 stdout 并在检测到 manifest 后立即 kill 进程组，是否有边界情况未考虑？例如：
   - Agent 在 manifest 之后还有必要输出（如 discussion reply）？
   - `select` 在 macOS 上的管道行为是否可靠？
   - stdin/stderr 后台线程的异常处理是否充分？

3. **NO_DIFF 问题**：Shield 的测试文件未被 git 跟踪导致 NO_DIFF。这是 Shield 应该自行 `git add` 还是需要 Runtime 在 diff 检测时也考虑 untracked 文件？

4. **M-TEST 阶段是否可以继续推进**：当前处于 NO_DIFF_REVIEW，Prism 正在评审。如果 Prism 通过，是否可以手动将测试文件加入 git 并推进到 M-IMPL？
