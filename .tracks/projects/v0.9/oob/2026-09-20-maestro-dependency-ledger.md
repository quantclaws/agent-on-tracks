# Maestro 依赖账本(v0.9 自主性差距分析)

> 目标(Human 指令 2026-09-20):v0.9 之后 runtime 自主推进开发,不再需要 maestro。
> 方法:记录每一次「没有 maestro 链条就会停」的干预——症状、根因、对应任务/issue、
> 无对应则立 FR。每项标注【已覆盖 by 任务/issue】或【缺口:需 FR】。

## 账本

### M-1 复链楔子:派发随进程死亡 → reviewer_dispatched 卡死,循环空转退出

- **时间**:2026-09-20 08:14–08:19 UTC(复链时)
- **症状**:`trac run` 启动后立即 rc=0 退出,无事件、无派发。状态 `M-IMPL/PRISM_FINAL active`,但 `reviewer_dispatched=True` + `pending=None` → decide() 返回 None(等待永不来的判决)。
- **根因**:昨天 23:51 链条被杀时一个 PRISM_FINAL 派发在飞。WAL pending 被后续事件清掉,但 dispatch 旗标只在 outcome 处理时复位——进程死亡路径没人复位。watcher 会重启循环,但循环每次都空转退出:**自愈链条断在「循环活着但无事可做」**。reducer 文档已认知此形态(machine_human_gates.py `_on_human_retry` 的 Review-substate parity 注释,run 01KZTHE7 2026-08-15 先例)。
- **干预**:`trac retry --actor Human --clear-evidence`(设计内命令,但需要有人知道何时用)。
- **归属**:【缺口:需 FR】——run_loop 启动时的恢复逻辑应检测「dispatch 旗标置位 + 无 pending + 无判决」楔子并自动复位(或 watcher 检测循环秒退+零事件时自动 retry)。这不需要人,是可机械判定的死锁形态。
- **拟 FR**:loop self-recovery from orphaned dispatch flags(检测楔子→自动 human.retry 语义→审计事件留痕)。

### M-2 复链前置:wheel 同步与进程拉起依赖人工

- **时间**:2026-09-20 08:13–08:19 UTC
- **症状**:复链三步(wheel 重建 → watcher → retry)全部人工执行。
- **根因**:链条停止期间的操作者语义(停链是人为决策,重启也人为)——但「操作者不在场时进程崩了」的场景由 watcher 覆盖(已在跑),真正的缺口只在**计划内停链后的复活**。
- **归属**:v0.9 任务图 T-014(服务装配与入口)/T-015(调度)的范畴——daemon 化/开机自启属于它们的 scope。watcher 24h 寿命上限(720×120s)也是一个可改进点:长停后 watcher 自己死了,需要人来重启 watcher。
- **拟 FR**:watcher 常驻化(launchd/cron 托管)或 T-015 调度器吞并 watcher 职责。

### M-3 环境变量传递:GITHUB_TOKEN 不在 watcher 的启动环境里

- **时间**:2026-09-20 08:19 UTC
- **症状**:watcher 拉起循环时不带 GITHUB_TOKEN/SSL_CERT_FILE/TRAC_GITHUB_REPO(脚本里没有);我人工拉起时带了。
- **根因**:watcher 脚本只做 `nohup .venv/bin/trac run`,环境裸继承。若 agent 需要 GitHub(建 issue 等)会失败。
- **干预**:人工带 env 拉起。尚未观察到 agent 实际需要它(T-002 的派发没碰 GitHub)。
- **归属**:【缺口:小】——watcher 脚本应 source 一个 env 文件,或 runtime 从宿主契约解析凭据。观察后续任务(带 issue_number 的)是否触发。

## 观察记录(尚不构成依赖)

- 2026-09-20 08:27 UTC:**#174/#172 文件投递通道首次实战闭环**——Prism 把 verdict 写入 inbox(合法两层 envelope),runtime 以 `envelope.file_delivered` 收集,零格式烧毁。格式死亡类问题从今天起应绝迹;后续死因只会是语义(好死)或 infra(可退避)。
- 2026-09-20 08:27 UTC:T-002 PRISM_FINAL 判 revise(2 条 blocker 级语义 findings:impl_defect 缺 summarize_readiness;test_defect 冻结锚零杀伤)→ 自动路由 Devon GREEN attempt 2。**注意**:finding②是 test_defect(冻结锚测试自身缺陷,Shield 域),但整判路由去了 GREEN(Devon)——观察 Devon 是否受 scope 约束无法处理②、是否需要 DIAGNOSE 再路由。若链条自己在 GREEN→PRISM_FINAL→DIAGNOSE 间正确流转,则无需干预;若卡住,记 M-4。
- 2026-09-20 08:27 UTC:Prism findings 的 evidence 字段为 None(schema 允许但削弱审计链)——低优先观察,若持续出现考虑 schema 收紧或 criteria 强调。

### M-4 Devon worktree 隔离逃逸:agent 绕过 worktree 直写主树

- **时间**:2026-09-20 08:27–08:29 UTC(T-002 GREEN attempt 2 第一次派发窗口)
- **症状**:worktree replay 零增量(replayed:false),但主树 `tracks/supervisor/readiness.py` 在窗口内被改——Devon 会话用绝对路径直写主仓(worktree cwd 约束没有拦住)。
- **runtime 的响应(全部正确)**:#173 漂移分区把它判为外来写入 → M7 换手 → watcher 重建接管;后续 WIP 播种把这份「逃逸增量」当作 main 的合法 WIP 播进下一次 worktree,工作没有丢失。
- **风险**:over-reach 审计只看 worktree 内的写入——直写主树的路径**绕过审计**(manifest 比对、allowed_paths 检查全部失效)。本次是善意写入(正确的实现),但同样的通道可以写任何文件。
- **干预**:无(链条自愈);但需立 issue。
- **归属**:【缺口:需 FR】——派发侧应把「主仓路径绝对写」纳入 over-reach 审计(worktree 派发时主仓的写域只允许 inbox 结果文件),或 agent 提示词明示 cwd 纪律 + 审计主树 diff。

### M-5 #174 落地缺消费方清单:Devon evidence 面没接文件通道(已修,含教训)

- **时间**:2026-09-20 08:29–08:44 UTC
- **症状**:Devon 按新合同完美投递(文件 schema 合法、字段齐全、回复一行指针),evidence 提取器只读回复文本 → evidence_malformed ×2 → 误入 S3「合同简化」仲裁。**合同没有 overstrain 写者——是 runtime 的读面聋了。**
- **修复**:extract_devon_evidence 文件 payload 第一优先(Prism 评审通过后提交);executor 收集面委托共享 declared_result_path 助手。
- **自主性观察(重要)**:**Archer 的 S3 RULING 独立诊断出了同一根因并裁定出正确方案**(「result_file is authoritative fallback,runtime hydrates backend extraction」)——链内的仲裁机制能自我诊断 runtime 缺陷;但**结构上无人能修**:Devon 的任务 scope 是 tracks/supervisor,runtime 自身代码(tracks/effects/**)不在任何 v0.9 任务的 scope 里。runtime 自修复 = 永远是 maestro/操作者的带外职责。
- **归属**:【结构性】——v0.9 后若要真正无 maestro,要么(a)接受 runtime 代码冻结、缺陷走 issue 队列等人工;要么(b)开一个「runtime 自维护」的特殊任务域(scope=tracks/**,评审与门禁更严)。这是 v0.10+ 的架构决策,不是 bug。
- **流程教训**:跨面接线(#174 类)的验收清单必须枚举**全部消费方**(收集面/判决面/evidence 面/审计面),不是只接最显眼的一个。

### M-1 补充:#175 已立(2026-09-20 08:4x)

### M-6 GREEN_GATE collect rc=2 瞬态失败 + 证据截断

- **时间**:2026-09-20 09:37:44 UTC(T-002 GREEN attempt 3 的 GREEN_GATE)
- **症状**:SELECT_TASK 的 [integration] 层收集 rc=2 → escalation(awaiting_human,attempts=2)。同一命令本地立即复跑 rc=0;错误文本被 `[:400]` 截断,stderr 里的真实 import 错误丢失,无法归因( stdout 的节点 ID 清单占了截断窗口)。
- **干预**:`trac retry --actor Human` 清 escalation 重试。
- **归属**:【缺口:需 FR】两个——(a) collect 失败的证据必须保留完整 stderr(截断保留尾部而非头部——错误在尾部);(b) 门禁瞬态失败(不可复现)应有有限次自动重试再升级。归 observation 类,不阻塞。
- **教训**:Green_GATE 前两次 attempt 的 worktree 都是零增量(Devon 对 test_defect 类 finding 无法行动)——路由保真度见 M-7。

### M-7 路由保真度:Prism 的分类写在散文摘要里,机器路由读不到 defect_classification 字段

- **时间**:2026-09-20 09:34 UTC(round-7 PRISM_FINAL revise → 机器路由 Devon GREEN,而 Prism 摘要明文「test_defect -> SHIELD_FIX」)
- **症状**:test_defect 类 finding 应路由 SHIELD_FIX(Shield 域),但 verdict 的 defect_classification 字段未携带该分类(只在 review_summary/findings 散文里),机器按缺省路由回 GREEN——Devon 连续两轮零增量空转(worktree replayed:false),烧派发。
- **根因**:schema 的 defect_classification 是可选字段;模型倾向把分类写进摘要文字。判据包(子状态路由语义)要求「以 assignment 注入的 vocabulary 与 schema 为权威」——但 schema 没有强制 findings 带分类 token。
- **归属**:【缺口:需 FR】PRISM_FINAL verdict schema 在携带 test_defect/red_defect 类 finding 时应要求 defect_classification 字段(schema 收紧),或路由器回退解析 findings[].classification。

### 状态小结(2026-09-20 11:2x UTC)

- M-1 → **#175**;M-4 → **#176**;M-6 → **#177**;M-7 → **#178**(M-7 的当日应急修复 = 提交 45c77fd)
- M-5(174b 消费方接线)已修复提交 093f28a;M-2/M-3 为观察级
- **通道切换**:Prism → napi-r/muse-spark(Human 2026-09-20,kimi-k3 额度数日);评审节奏 300-800s → 150-170s
- **T-002 完成**(11:12 UTC,3/16);Shield 修复提交 5d06e7b(冻结锚 83 行改写 + 顺带落了 T-001 滞留的 RED 文件);链自动进入 T-003
- infra 等待语义对齐决策(提交 49181da:阈值 3→可调默认 12,≈106 分钟自等待)

## 断点预案(2026-09-20 ~12:00 UTC,maestro 会话因网络切换中断)

- 链进程(loop/watcher)经 nohup+disown 脱离会话,**不随 maestro 会话死亡**;全部状态在事件账本+git(HEAD 4b25336)持久。
- 网络中断期:在飞 agent 派发会崩 → infra 分类 → 退避自等待(上限 ~106 分钟,提交 49181da)→ 网络恢复即自动续跑,零人工。
- 恢复检查单(maestro 回来后):① `trac status`——active(已自愈)或 awaiting_human(中断超 ~106 分钟,执行一次 `trac retry --actor Human`);② watcher 存活(24h 寿命,2026-09-21 ~08:16 UTC 到期,过期则重启 `nohup zsh .tracks/runtime/drift-restart-watcher.sh &`);③ `tail .tracks/runtime/logs/trac-run.log` 对账进度。
- 当刻快照:3/16 完成;T-003 PRISM_RED 在飞(pid 69194)。

## 夜间批次记录(2026-09-20 深夜,maestro 值守)

- **31da17d**:DIAGNOSE 判决面接结果文件(174b 镜像)——评审 PASS;修复经 watcher 热修路径在提交前已实战生效(attempt 2 判决正确入账)。
- **898adcd**:tracks-devon-rgr v0.5——skill 教的 pytest.fail 守卫在门禁分类器那里是 unclassified 非法;改教 raise AssertionError。deepseek 通道照书抄才暴露(flash 模型的高保真是双刃剑)。
- **016e8f2**:任务预算按任务过滤——计数无任务过滤导致 T-008(零自身失败)被三连拒 + 雪球;修复后 T-008 立即收口。
- **M-8(新)**:任务预算全局计数 bug——同上,归 #178 路由保真同族?不,独立类:门禁判据的归属域错误。已修,无需 FR。
- 进度:T-004(16:52)、T-007(17:36)、T-008(19:22)相继收口——**7/16**;T-009 RED 已交复审。
- 通道健康:deepseek-flash Devon 稳定(GREEN 平均 ~3-8 分钟);Prism muse-spark 稳定;step5(Shield)未被派发过,无限流。

### M-9 通道余额死亡(deepseek,2026-09-20 23:04-23:19 UTC)

- **症状**:Devon(deepseek-v4.1-flash)连续 6+ 次快速崩溃(75-83s),blob 证据 `APIError: insufficient balance, 429`——账户余额耗尽,与限流不同,**等待不可恢复**。
- **runtime 行为**:infra 退避按设计爬到 900s 封顶,streak 12(≈00:15 UTC)后将升级停车(awaiting_human)。
- **干预边界**:Shield 换道有授权(zhipu/glm-5.3-flash,01:00 UTC 前);**Devon 无换道授权**——停机等用户裁决(充值 deepseek 或授权换道)。
- **元观察**:这是今天第三个通道级死亡(kimi 配额→计划降级→deepseek 余额)。多 provider token 平衡是运营常态;T-003 落地的等待/quota 机制正是为此而生(产品吃自己的狗粮,但余额类等待语义需要区分——余额耗尽应快速升级而非等满 106 分钟,可作为 T-003 的 follow-up 观察)。

### M-10 Prism 通道无法完成终局 DIAGNOSE(2026-09-21 17:29-20:10 UTC)

- **症状**:T-018 的 DIAGNOSE 连续 5+ 次全部在 1504-1522s(≈25 分钟整)死于 `zen-sticky: all upstream attempts fail (conn:timeout)`——muse-spark 上游对长连接有 ~1500s 硬超时,而终局收口的取证调查(读全图)天然超过它。
- **已试杠杆**:时间纪律条款(fd43060,objective 里明确 15 分钟收敛+只读证据文件)——**无效**,模型照跑 25 分钟(提示词是弱杠杆的又一实证)。
- **待决**:Prism 换通道(用户拍板;glm-5.3/deepseek-flash 有余额)。链条按 infra 退避循环,streak 12 后安全停升级,无损失。
- **伴随发现**:watcher 24h 寿命到期曾造成 ~3h 无人接管(累计 #713 次重启,大部分是 M-4 逃逸换手税)——watcher 常驻化(#175 关联)优先级应升。
