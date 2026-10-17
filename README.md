# 子午汐律 · GitHub Actions 统一调度中心

本公开仓库 **只保存通用调度程序、明确允许公开的仓库/工作流标识以及不涉及业务隐私的状态**。

- **不**检出、下载、上传或复制 `Test`、`Stock`、`database` 的私有源码、仓位、账本或 APK 签名密钥。
- **不**在调度日志或 Git 中保存个人访问令牌、策略明细、交易记录、完整私有工作流日志。
- GitHub 托管 Ubuntu 负责云端调度；Windows 的自托管 Runner 只在其目标私有仓库中执行。
- 每个派发请求先核实目标仓库身份、工作流、业务日期、任务唯一键、账户用量和 Windows Runner 的在线/繁忙状态。
- 额度使用量与剩余额度不同：没有可靠余额证明时输出 `unknown`；不能把 API 失败当成免费额度耗尽。
- 同一任务只能有一个活跃执行或已核验产物。失败补偿必须先检查实际副作用和上游数据版本。

## 当前状态

**受限线上适配器与实时证据门已实现（2026-10-17）**：新增固定 API 路径的读写适配器，以及有截止时间的同一前台监控循环；实际命令入口只使用 `ReadOnlyPorts`，写适配器和监控循环均未接入生产任务。仅用 Actions 的原 Run、准确日期 Job 与最终就绪步骤核对候选源码和 300 秒时效，不要求私有 Contents 或 Commit statuses 权限，不能复用历史诊断作为就绪证明；正式 Run 源码必须在绑定前匹配候选，私有准入才允许业务；A/US 任一活跃运行都会挡住新的共享账本任务。[线上只读验收 #39010081814](https://github.com/chaonpu/Runner/actions/runs/39010081814) 成功，**232 项测试通过**，现有令牌读取私有 Run/Job 元数据通过。私有实时证据生产器尚未安装，缺失证据保持暂停；不会因此启用生产。详见[接线与边界说明](docs/production-live-ports.md)。

**生产协调核心隔离演练通过（2026-10-17）**：新增 `scheduler/production_session.py`，补齐预约确认、唯一派发调用、原 Run 绑定和逐次前台观察的协调顺序。**本地完整回归 201 项通过**，其中新增 25 项；与当前私有准入/回执链的 10 个合成契约检查通过。网络响应不确定、CAS 冲突、源提交变化均不自动重发；运行成功只到 `awaiting_effect_verification`。模块没有线上适配器、CLI 或工作流接线，未产生真实派发、正式数据库写入或生产回执。生产开关及 Windows 生产白名单继续关闭。完成范围与剩余接线要求见[演练说明](docs/production-session-rehearsal.md)。

**Windows Git 连接处理已接入并验收（2026-10-17）**：[受控验收 #39008535752](https://github.com/chaonpu/Stock/actions/runs/39008535752) 成功。任务启动时优先验证正常路由，必要时仅在当前 Job 使用经过 GitHub 官方地址范围、TLS 和重复读取校验的备用路由，并使用 HTTP/1.1；不改 Windows hosts 或全局配置。Stock 与 database 的真实 fetch、push --dry-run、主分支/预演引用不变、各 3 次连续读取均通过；现有缓存工作区的生产同版 Checkout 源码身份及 Windows 12 项测试通过。修复接入 A/US 及 3 个 Windows 验收工作流，连接失败在策略执行前停止；Windows Checkout 每次最多 4 分钟，重试前重新预检路由。正式 database 未修改；推送预演不等于真实提交或生产任务闭环。生产开关与 Stock Windows 白名单继续关闭。

**临时数据库账本验收通过（2026-10-17）**：美股配置的完整持仓与历史已确认和已结算权威账本一致，无需回退。修复私有快照丢失实际仓位规则、被账本误按旧规则拒绝的问题。[Windows 验收 #38999476492](https://github.com/chaonpu/Stock/actions/runs/38999476492) 成功，80 项回归通过；用原客户端补齐日线后，已在临时数据库副本将日期从 2026-10-15 推进至 16 日。重复执行处理 0 条且文件不变，历史净值及已清仓记录保留，下一交易日开盘再平衡仍待执行，另一市场未改变。同一提交回执关联通过的是明确标记的模拟来源，真实诊断 Run 被拒绝充当生产回执。正式 database 和配置没有写入，私有数据未进入本仓库。Windows 直连 Git 拉取曾失败，API 下载成功不代表生产推送链路恢复；Ubuntu staging 本轮未启动，不能视为通过。Git 传输的后续受控验收见上方最新记录；真实生产任务闭环仍待验收，所有生产开关与 Windows 生产白名单继续关闭。

**Windows 完整隔离计算验收通过（2026-10-17）**：A股 [Run 38993120469](https://github.com/chaonpu/Stock/actions/runs/38993120469) 成功、117 项回归通过；美股 [Job 117276484225](https://github.com/chaonpu/Stock/actions/runs/38991846981/job/117276484225) 成功、83 项回归通过。两侧结果日期均为 20261016、核心缺失 0，原始跟踪文件未改变。下载私有产物后已独立核对演练回执与结果 SHA256、源码和 Run ID 一致。A股周六的正式报告限制未绕过：计算完成，`formal_orders_allowed=false`、正式报告未生成。发现并修复 ETF 已富化快照重复 merge 导致基金类型标准列丢失的问题，3 项针对回归及完整计算均通过。产物仅保留在私有 Stock，未写正式 database、未发邮件；去重/回执生产契约通过的是合成测试，尚非真实生产入账。后续已确认该差异来自原始快照未结算、权威 ledger 已结算，配置与账本一致，详见上方最新验收。当前完整验收工作流仅手动，生产开关与 Windows 生产白名单仍关闭。

**Windows 真实行情样本验收通过（2026-10-17）**：私有 [Stock #38990879940](https://github.com/chaonpu/Stock/actions/runs/38990879940) **Success**。指定 Windows Runner 身份、首次源码拉取、隔离安装正式依赖和 5 项验收测试通过；7 类样本读取全部通过，覆盖 A股股票/ETF/概念日线、三级行业，以及美股报价与原始/前复权日线、官方概念日线和两份挂牌目录，日线末日均为 20261016。缓存隔离，未执行策略 main/run、未写 database、未发送邮件；跟踪的生产文件无改动。一次性部署触发已移除，新私有 `windows-live-source-acceptance.yml` 仅手动运行。此结果只证明样本数据源兼容，**尚不证明全池策略或入账**；两个市场 `windowsWorkflow=null`、生产派发开关仍关闭。下一阶段为隔离的完整策略计算和任务/回执验收。

**2000 分钟与公开仓库排除口径已修正（2026-10-17）**：用户已确认个人账户每月免费额度为 **2000 分钟**，现已写入两个账号配置。不能将账户总用量超出 2000 的部分当作私有额度消耗。新增按仓库拆分的 `/users/{owner}/settings/billing/usage` 读取，使用相应 Actions 令牌仅 GET 仓库元数据，排除公开仓库标准 GitHub-hosted Runner 的分钟；同时兼容线上实际返回的仓库短名。公开用量与账户包含额度均可能计入 discountAmount，因此不把折扣总量当作额度抵扣。私有标准 Linux 用量形成余额估算；其他私有运行器折算未核实时保持未知，不猜倍率，也不将当前可见性下的估算伪装成实时精确余额。[线上检查 #38990659922](https://github.com/chaonpu/Runner/actions/runs/38990659922) 成功，**176 项测试通过**。两个账号在排除公开标准用量后，剩余估算均为零；`chaonpu` 记录中确有公开标准用量被排除。公开日志只输出分类与布尔状态，不披露仓库账单明细和消耗数值。精确余额验证仍为 false；生产派发、登记/数据库写入、自动重试均保持关闭。

**任务额度门槛已部署（2026-10-17）**：[线上检查 #38990217463](https://github.com/chaonpu/Runner/actions/runs/38990217463) 成功，**168 项测试通过**。云端推荐现要求经独立核实的 `remainingFreeMinutes` 足够覆盖任务 `estimatedMinutes + reserveMinutes`，证据 `observedAt` 为带时区时间、距检查时刻不超过 300 秒且不在未来，`period` 必须是当前 UTC 年月。不足只表示本任务预算不足，不把账户判为耗尽；未知、过期、跨月、负数、非有限数及缺字段均不推荐云端。可用和耗尽证据均受时效检查；耗尽还要求余额确为零。当前 Billing Usage 适配器仍不提供该独立余额证据，因此不因新门槛而启用云端派发。计费 GET 已显式传入当前 UTC year/month，并验证返回的 timePeriod；两个账户线上 `reportingPeriodVerified=true`、用量可读，但剩余额度继续 `unknown`。Windows Runner 在线空闲，A/US 无活跃运行，生产开关保持关闭。

**Runner 令牌修复已线上验收（2026-10-17 09:12 北京时间）**：[安全检查 #38989843184](https://github.com/chaonpu/Runner/actions/runs/38989843184) 成功，代码提交 `4356c58` 将 Stock 所属账号的 `runnerSecret` 改为用户已核对的 `RUNNER_READ_TOKEN`；`CHAONPU_BILLING_READ_TOKEN` 继续只读计费用量，`CHAONPU_ACTIONS_READ_TOKEN` 用于 Actions 查询。**159 项测试通过**。实际只读结果：A/US 的 `runnerRead=readable`、`windowsOnlineAndLabelsVerified=true`（指定 Runner 在线、空闲且标签匹配），两个工作流均 `no_active_run`。两账号 Billing Usage 可读，但剩余免费分钟仍未核实；Stock 的 Windows 生产工作流尚未完成业务验收，因此仍未加入调度白名单。生产派发、登记写入、database 写入及自动重试全部关闭。Test 的 Runner 凭据映射本次未改。

**最新线上联通结果（2026-10-17）**：[RUNNER #38974929403](https://github.com/chaonpu/Runner/actions/runs/38974929403) 已通过现有安全 CI 完成一次性**真实 GitHub Contents API 只读读取**，`registryApiRead=true`、生产任务 `0`、A/美股自动候选日期均为 `2026-10-16`、路由建议均 `deferred`，生产派发/任务写入/账本读取/自动重试均为 `false`。同轮 **125 项测试通过**。临时线上联通步骤已移除，后续代码提交不会持续检查业务状态。[移除后 CI #38974990095](https://github.com/chaonpu/Runner/actions/runs/38974990095) 成功。注意此次是借助原本 `validate.yml` 执行的真实在线只读验收，并非 `production-dispatch.yml` 手动启动的完整验收。正式策略开关持续关闭。

**安全验收新入口（2026-10-17）**：新增 [production-dispatch.yml](.github/workflows/production-dispatch.yml)，当前**仅人工 workflow_dispatch + contents:read**，支持 A股、美股**不填日期即可执行只读预检**。默认使用实际运行时刻推算市场时区下最近已完成的周一至周五收盘日：A股为北京时间 15:00，美股为纽约时间 16:00（自动适配夏令时）；周末和盘中回退到前一工作日。需要审计历史时仍可手工填写日期。**这只是日期候选，不包含交易所节假日/提前收盘核实，不能作为真实派发许可**。Python 入口 `scheduler.production_dry_run` 从公开 GitHub Contents API GET 当前 `state/tasks.json`，发现异常直接 hold，即使预检推荐 cloud 也永远输出 `dispatchAuthorized=false`、`registryWriteAuthorized=false`、`databaseWriteAuthorized=false`、`automaticRetryAuthorized=false`。没有任何跨仓库派发、状态写入、账本同步或定时触发。GitHub Base64 换行兼容已修复。[RUNNER 测试 #38974308079](https://github.com/chaonpu/Runner/actions/runs/38974308079) **121 项通过**。该入口**尚未真实手动执行**，不能把单元测试视为在线预检结果。

**目前已部署：云端按需预检 + 调度启动后同一 Job 前台监控。** Stock 的无操作试验通过 `stock-noop-pilot.yml`：先登记并派发确定 Run ID，随后每约 30 秒查询状态；目标任务结束后写回 `completed_success` 或 `completed_failure` 并停止监控 Job。**已移除独立五分钟 `schedule`。** `reconcile.yml` 仅保留手动异常恢复入口，正常运行不使用。正式策略自动派发仍关闭，避免重复生产计算。

**最新验收（2026-10-17）**：Stock 无业务测试 [Runner #38967023305](https://github.com/chaonpu/Runner/actions/runs/38967023305) → [Stock #38967039826](https://github.com/chaonpu/Stock/actions/runs/38967039826)，已走通登记、唯一派发、同一 Job 持续监控、自动结果回写及退出；两笔公开 canary 登记均为 `completed_success`。原五分钟 cron 已删除，现有 Runner 工作流由安全测试防止重引入定时触发。公开生产任务 CAS 状态存储、只读预检和防重门禁已实现，但**正式生产调度未启用**。[RUNNER 安全测试 #38969991137](https://github.com/chaonpu/Runner/actions/runs/38969991137) **106 项通过**。

**私有执行回执（2026-10-17）**：`chaonpu/Stock` 的 A/US 发布步骤已经加入**显式 task_id 才启用**的同一数据库提交回执。无新市场版本则不会伪造回执，普通不带 task_id 的旧策略不改变。私有核验检查源 Run、数据库 Git 历史、回执、快照/账本及最新版本哈希。[Windows 合成测试 #38972033613](https://github.com/chaonpu/Stock/actions/runs/38972033613) **47 项通过**。但 GitHub REST 可能不返回工作流原始输入，且尚未核对公开 Runner 登记，所以验证器明确保持 `coordinatorVerified=false`、`businessVerified=false`、`safeToRetry=false`。私有核验工作流仍为手动执行，**不得把模拟成功视为真实生产入账或允许自动补算**。详见 [部署计划](docs/deployment.md)。

当前凭据与权限边界：

- `TEST_ACTIONS_READ_TOKEN`：Test 私有仓库 Actions 读写；`CHAONPU_ACTIONS_READ_TOKEN`：仅 Stock 与 ziwu 签名私有仓库 Actions 读写。两者虽然名称含 READ，但已验证具有私有仓库的 `workflow_dispatch` 写入能力。**不授权公开 `chaonpu/ziwuxilv`**。
- `TEST_BILLING_READ_TOKEN`、`CHAONPU_BILLING_READ_TOKEN`：仅用于对应个人账户 Billing Usage GET。
- `TEST_RUNNER_READ_TOKEN`、`RUNNER_READ_TOKEN`：仅用于目标仓库的 Runner 状态 GET。

`ZIWUXILV/database` 当前仅参与 Stock 的私有数据依赖，不由调度中心派发工作流，因此不要求该组织的 Billing、Actions 或 Runner 令牌。

目标阶段：安全完成 Stock 单任务演练后，再逐项串联 Test、Android 私有签名。公开正式版由 `chaonpu/ziwuxilv` 自己的 `publish-android-release.yml` 负责发布，在私有签名产物经核验后通过独立发布请求触发，**不使用 Runner 的私有仓库令牌跨仓库派发公开发布工作流**。详见 `docs/deployment.md`。

## 使用入口

- [正式生产只读安全验收入口（手动；不可派发）](https://github.com/chaonpu/Runner/actions/workflows/production-dispatch.yml)
- [云端按需预检（手动）](https://github.com/chaonpu/Runner/actions/workflows/scheduler.yml)
- [Stock 持久化防重派发演练（手动，仅无操作 canary）](https://github.com/chaonpu/Runner/actions/workflows/stock-noop-pilot.yml)
- [手动异常恢复核对（非定时）](https://github.com/chaonpu/Runner/actions/workflows/reconcile.yml)
- [经批准签名 APK 公开暂存（手动，Pre-release）](https://github.com/chaonpu/Runner/actions/workflows/stage-android-apk.yml)
- [安全单元测试](https://github.com/chaonpu/Runner/actions/workflows/validate.yml)
- [完整部署状态与授权要求](docs/deployment.md)

**正式策略／构建派发尚未启用**：已有的 `TEST_ACTIONS_READ_TOKEN`、`CHAONPU_ACTIONS_READ_TOKEN` 虽名称含 READ，但用户确认它们具备 Actions:write，已在 Stock 和 Test 两次真实无业务副作用 canary 中验证成功。仍需业务工作流的任务编号、核验、幂等适配，以及可靠额度判断。

## APK 暂存与正式发布分离

新 APK 经 `Test` 完整验证和 `ziwuxilv-android` 私有生产签名后，管理员可以使用版本专用批准口令手动将**签名 APK 和校验清单**存放到 `Runner` 的公开预发布 Release，创建匿名 HTTPS 下载地址。需要新版本、精确的 Test/签名 Run、源码 SHA、生产证书及 APK 哈希核验。**此操作会提前公开 APK，请勿在未获准时触发。**

这不更新用户 APP：正式版 `chaonpu/ziwuxilv` 仍须独立审批和发布请求。流程详见 [完整部署说明](docs/deployment.md)。历史 v2.3.4 不会自动重新暂存或发布。

## 私有 Stock 防重复派发演练

`.github/workflows/stock-noop-pilot.yml` **只允许 `chaonpu` 从 `main` 手动启动**。填写测试日期（YYYY-MM-DD），先将 `pilot:stock:日期` 原子登记到公开的 `state/pilot_runs.json`，然后仅派发私有 `chaonpu/Stock/.github/workflows/runner-dispatch-canary.yml`（无源码检出、无策略、无 database 操作、无邮件）。返回的实际 Run ID 继续写入同一登记文件，然后**同一调度 Job**通过只读 Actions API 前台监控，任务结束时确认目标 Run 并写回成功／失败状态，随后停止。若 Job 中断、断网或超时，原任务保留待核对状态，允许再次手动选择 `mode=monitor_only` 只监控旧 Run，绝不重新派发。

同一日期的再次请求会停止，不会盲目重复派发；写入响应未知时需人工检查登记表和运行页面。测试日期只是公开演练标签，**不是交易日期计算结果**。

正式策略的 `dispatchEnabled` 和 `enabledForDispatch` 仍全部关闭。不得把 canary Success 当成正式账本已入账。
