# 受限线上适配器与实时证据门（2026-10-17）

本轮在隔离协调核心之外增加真实 GitHub API 适配器，实际命令入口只构造 `ReadOnlyPorts`。`ProductionPorts` 的状态写入和派发方法，以及 `production_watch.watch()` 的前台循环，都没有接入生产工作流。总开关、任务开关、Stock Windows 白名单保持关闭。

## 读取与写入范围

| 组件 | 已实现行为 | 当前线上接线 |
| --- | --- | --- |
| `production_ports.ReadOnlyPorts` | 固定公开仓库的配置/登记读取；私有源码提交身份、运行状态、Runner、账户预算及专用准备状态元数据读取 | `production-dispatch.yml` 仅人工、只读调用 |
| `production_evidence.inspect` | 仅用 Actions 元数据，按任务日期、来源 Run、就绪 Job/步骤和 300 秒时效核实证据及候选源码 | 只读调用；私有证据生产器尚未安装，缺失即暂停 |
| `production_ports.ProductionPorts` | 固定任务登记路径 CAS；限定 A/US 两个私有工作流的单次 POST；禁止任意仓库/工作流/重跑/取消 | 未接线；只用模拟 HTTP 测试 |
| `production_watch.watch` | 同一前台调用观察原 Run；默认每 30 秒，4 小时截止，可设上限 5.5 小时；任务结束或不确定响应时停止 | 未接线；只用模拟时钟测试 |

公开中心不会请求私有仓库 Contents、源码、Git 树、产物或日志。公开输出使用固定字段，保留就绪/暂停、日期、读取状态和生产关闭状态；不会复制远程响应的任意字段、私有策略输出、持仓、账本或错误正文。

公开配置和登记从同一个固定 main 提交读取，并检查 Base64 内容的 Git blob SHA。私有证据路径仅请求 Actions API，不要求私有 Contents 或 Commit statuses 权限。A/US 共用私有发布锁，因此任一业务工作流活跃或状态不可核实都会挡住新的生产任务。

准备 Run 的 `head_sha` 是已经验证来源的候选源码，不声称它仍是当前 main。正式派发后，必须先确认实际 Run 的源码与候选完全一致，才能写入 Run 绑定；私有准入在业务开始前等待这个准确绑定。因此 main 在准备后变化会留下未绑定预约并暂停，不能执行错误源码。这里不会通过扩大私有 Contents 读取权限来获取当前 main。

## 单次派发约束

写适配器只允许原始管理员 `workflow_dispatch`、main、第一次尝试和准确的生产协调工作流上下文。配置必须在线复核为允许；预约必须经过原有规则校验、CAS 保存和读回确认。

只有同一适配器实例刚刚保存并确认的预约，才可使用一次派发资格。实例重建不能派发旧预约；资格在发送前即被消耗。派发前再次检查源码、准备证据、路由及登记。超时、丢失响应或缺少确定 Run ID 均不重发。绑定必须是本实例实际收到的原 Run ID，且元数据核对一致。成功运行仍仅进入 `awaiting_effect_verification`，没有任何 `verified` 写入能力。

前台监控不注册后台服务或五分钟定时任务。不确定的查询或状态回写会立即停止，避免重复已经可能被接受的写操作。另一次人工工作流不能冒用原协调者继续写入；独立异常对账入口仍需单独设计。

## 私有实时证据协议

接收器只接受私有 Stock 对应市场最新的专用准备 Run：`.github/workflows/production-readiness-a.yml` 或 `production-readiness-us.yml`，限定管理员手动启动、main、首次尝试且成功结束。最新运行失败或未结束时，不回退到更旧的成功证据。

该 Run 必须有唯一成功的 `readiness-v1:任务:YYYY-MM-DD` Job，及唯一成功的 `Attest live readiness` 步骤；步骤结束时间为证据时间，距检查不超过 300 秒，必须落在原 Run / Job 的真实时间范围内。Run 更新时间不能延长旧步骤证据的寿命。已知输入冲突、缺失/重复 Job、任务日期不符、过期、未来时间、失败或查询异常均暂停。旧诊断、行情样本、完整隔离计算、离线账本回放均不能冒充此专用证明。

**该私有证据生产器尚未安装。** 后续需要在私有环境独立核实实际交易会话已结束、完整实时行情输入、完整选股输入，全部与任务日期和源码一致，才能完成就绪证明步骤。本次没有用日历候选、七项行情样本或历史验收来填充这些事实。专用准备工作流不存在、不可见或没有证据时，入口明确暂停。

API 协议参考：[GitHub workflow runs](https://docs.github.com/en/rest/actions/workflow-runs) 与 [workflow jobs](https://docs.github.com/en/rest/actions/workflow-jobs)。第一轮线上只读验收确认现有 Actions 令牌不能读取私有分支元数据，因此最终协议改为仅使用 Actions 权限；未修改任何令牌权限。

## 本轮验证

本地完整回归 **232 项通过**，新增 31 项，包括 API 路径约束、只读入口不能构造写适配器、Git blob 校验、失效证据、源提交变化、跨市场活跃任务、接受预约后响应丢失、派发超时、旧预约不能重发和前台监控截止。

线上只读验收 [CI #39010081814](https://github.com/chaonpu/Runner/actions/runs/39010081814) 成功，232 项测试通过。使用现有 Actions 令牌读取真实私有 Run 及 Job 元数据成功；两个市场均自动解析为 2026-10-16，无活跃业务运行、登记 0，生产开关关闭。专用私有准备工作流尚未安装，两者均返回 `hold_private_readiness_workflow_missing_or_not_visible`；真实就绪证明未生成，未启动策略。临时 CI 验收步骤随后移除，人工只读入口保留。生产登记、正式数据库、私有策略工作流及邮件未因本轮更新而执行或改变。详细固定字段见 [验收记录](production-live-ports-20261017.json)。
