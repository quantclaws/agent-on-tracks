---
name: tracks-quality-guards
version: 0.1
description: 宿主项目工程质量守卫栈的目录与安装分工——lint/format、静态检查、认知复杂度、文件/方法长度、重复度、覆盖率门槛、pre-commit 钩子运行器与 CI required checks。当 Archer 设计 architecture.md 的 machine contracts（选定守卫、写安装命令、声明 Scaffold 宣言中的守卫配置文件）或 Prism 审查守卫完整性时使用。
---

# tracks-quality-guards：宿主工程质量守卫栈

质量守卫不是实现阶段的补丁，而是设计期的 machine contracts。tracks 自身的守卫栈（本仓库 `.githooks/pre-commit`、`.flake8`、`pyproject.toml`）是 canonical 示例：Python 宿主直接照抄，其他语言按同一套守卫类别映射到宿主生态的惯用工具。

## 1. 原则——完整守卫栈，逐项入合同

每个宿主项目在设计期（M-DESIGN）必须得到一套**完整**的守卫栈，八类缺一不可：

1. **lint + format**：风格与明显错误的机器拦截，格式化可自动修复。
2. **静态检查**：类型/语义层检查（语言有类型系统时必须）。
3. **认知复杂度**：分支/嵌套深度的单一度量（取代单独的嵌套深度检查）。
4. **文件长度**：单文件行数上限。
5. **方法长度 / 局部变量**：单函数语句数与局部变量数上限。
6. **重复度**：跨文件重复块检测。
7. **覆盖率门槛**：测试覆盖率阈值，低于即失败。
8. **钩子运行器 + CI required checks**：pre-commit 在本地提交时执行全部守卫；同一套守卫在 CI 中作为稳定的 required checks 再次执行——merge 只认 CI 结论。

合同要求：

- 守卫以 machine contracts 写入 architecture.md（外部可观察 + 责任方），并在 Scaffold 宣言中列出每个守卫的配置文件。
- gate 证据必须来自守卫的**真实执行**（CI required checks 的通过/失败输出），不接受文档声明或自述。
- 任何角色不得安装或修改 hook 绕过门禁（flow.md §8 硬规则 2）；放宽阈值等于变更合同，必须先修订设计文档，不得静默削弱。

## 2. 分语言目录

### 2.1 Python（canonical：tracks 自身配置）

| 守卫 | 工具（pinned） | 配置位置 | 阈值 | 执行点 |
| --- | --- | --- | --- | --- |
| lint + format | ruff==0.16.0 | `pyproject.toml` `[tool.ruff]` / `[tool.ruff.lint]` | line-length=100；select E,F,W,I,B,UP,SIM,C4 | pre-commit + CI |
| 认知复杂度 | flake8==7.3.0 + flake8-cognitive-complexity==0.1.0 | `.flake8` | CCR001，max-cognitive-complexity=15 | pre-commit + CI |
| 重复度 | pylint==4.0.6 | `pyproject.toml` `[tool.pylint.similarities]` | R0801，min-similarity-lines=5（忽略注释/docstring/签名） | pre-commit + CI |
| 文件长度 | pylint | `[tool.pylint.format]` | C0302，max-module-lines=1000 | pre-commit + CI |
| 方法长度 / 局部变量 | pylint | `[tool.pylint.design]` | R0915 max-statements=50；R0914 max-locals=15 | pre-commit + CI |
| 覆盖率门槛 | coverage==7.15.2 + pytest==9.1.1 | `pyproject.toml` `[tool.coverage.*]` | CI 中 coverage report --fail-under（阈值入合同，tracks 惯例 ≥95%） | CI |
| 钩子运行器 | git hooks（hooksPath=.githooks） | `.githooks/pre-commit` | 按序执行上述全部检查 | 本地提交 + CI |
| CI required checks | CI workflow | architecture.md CI 合同 + workflow 文件 | 守卫 job 全部 required、名称稳定 | merge 门禁 |

要点：pylint 只启用重复/长度/局部变量四个码（`disable=all, enable=R0801,C0302,R0915,R0914`），其余 lint 一律归 ruff，避免双重风格裁决；认知复杂度归 flake8 单点负责。测试代码可豁免方法长度/局部变量（线性脚本），但重复度与文件长度不豁免。

安装命令（Archer 写入 machine contracts；安装本身是 M-IMPL foundation task）：

```
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
git config core.hooksPath .githooks
```

### 2.2 JavaScript / TypeScript

- lint + format：ESLint（typescript-eslint）+ Prettier；复杂度可挂 eslint 的 complexity 规则或 sonarjs 认知复杂度。
- 重复度：jscpd；文件/方法长度：ESLint max-lines / max-lines-per-function。
- 覆盖率：c8 / istanbul（--check-coverage 阈值入合同）。
- 钩子：tracks 自带 `templates/pre-commit/node.yaml`（mirrors-eslint）。

### 2.3 Go

- format：gofmt/goimports；lint + 复杂度 + 重复 + 长度：golangci-lint（cyclop 认知复杂度、dupl 重复、funlen 函数长度）。
- 覆盖率：`go test -cover`（阈值脚本化入 CI）。
- 钩子：tracks 自带 `templates/pre-commit/go.yaml`（go-fmt / go-unit-tests / golangci-lint）。

### 2.4 Rust

- format：rustfmt；lint + 复杂度：clippy（cognitive_complexity、too_many_lines、too_many_arguments）。
- 覆盖率：cargo-llvm-cov（阈值入合同）。
- 钩子：tracks 自带 `templates/pre-commit/rust.yaml`（fmt / cargo-check / clippy）。

### 2.5 其他语言

按宿主事实适配：在宿主生态中为八类守卫各选惯用工具，写出 pinned 版本、配置位置与阈值；某类确无可用工具时，把缺失作为显式设计决定记录在 architecture.md，不得静默留空。tracks 还提供 `templates/pre-commit/base.yaml`（语言无关钩子：trailing-whitespace、check-yaml/toml、merge-conflict、large-files）、`java.yaml` 与 `ci-snippet.yml`（CI 中运行 pre-commit 的片段）。

## 3. 安装分工（设计 vs 实现）

- **Archer（DECIDE）**：选定守卫栈，把每项守卫的精确安装命令、配置内容、阈值写入 architecture.md 的 machine contracts；守卫的配置文件（如 `.flake8`、`pyproject.toml` [tool.*] 段、`.githooks/pre-commit`、CI workflow）在 Scaffold 宣言中逐文件声明，Archer 在 M-DESIGN 落盘这些声明/配置本身。
- **Devon（M-IMPL foundation task）**：在合同下执行安装（建 venv、装 pinned 工具、`git config core.hooksPath`、注册 CI required checks），并用真实运行输出补全证据；实现/安装不新选工具、不改阈值。
- **证据语义**：gate 证据 = 守卫真实执行的输出（CI required checks 状态）；只有声明而无执行证据视为守卫缺失。
- **不可削弱**：任何角色不得安装或修改 hook 绕过门禁；阈值变更必须回到设计文档修订（flow.md §8 硬规则 2）。

## 4. architecture.md 记录格式

machine contracts 中每项守卫记录五要素：**守卫**（八类之一）、**工具**（含 pinned 版本）、**配置位置**（文件 + 段）、**阈值**、**执行点**（pre-commit / CI required check 名称）。示例行：

- 认知复杂度 — flake8 7.3.0 + flake8-cognitive-complexity 0.1.0 — `.flake8` — CCR001 ≤ 15 — pre-commit + CI `lint` required check。

Scaffold 宣言与 machine contracts 必须互相引用闭合：宣言里的每个守卫配置文件，在 machine contracts 中都有对应的五要素条目。
