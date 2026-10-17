# 子午汐律 · GitHub Actions 统一调度中心

本公开仓库 **只保存通用调度程序、明确允许公开的仓库/工作流标识以及不涉及业务隐私的状态**。

- **不**检出、下载、上传或复制 `Test`、`Stock`、`database` 的私有源码、仓位、账本或 APK 签名密钥。
- **不**在调度日志或 Git 中保存个人访问令牌、策略明细、交易记录、完整私有工作流日志。
- GitHub 托管 Ubuntu 负责云端调度；Windows 的自托管 Runner 只在其目标私有仓库中执行。
- 每个派发请求先核实目标仓库身份、工作流、业务日期、任务唯一键、账户用量和 Windows Runner 的在线/繁忙状态。
- 额度使用量与剩余额度不同：没有可靠余额证明时输出 `unknown`；不能把 API 失败当成免费额度耗尽。
- 同一任务只能有一个活跃执行或已核验产物。失败补偿必须先检查实际副作用和上游数据版本。

## 当前状态

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
