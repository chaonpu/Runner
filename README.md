# 子午汐律 · GitHub Actions 统一调度中心

本公开仓库 **只保存通用调度程序、明确允许公开的仓库/工作流标识以及不涉及业务隐私的状态**。

- **不**检出、下载、上传或复制 `Test`、`Stock`、`database` 的私有源码、仓位、账本或 APK 签名密钥。
- **不**在调度日志或 Git 中保存个人访问令牌、策略明细、交易记录、完整私有工作流日志。
- GitHub 托管 Ubuntu 负责云端调度；Windows 的自托管 Runner 只在其目标私有仓库中执行。
- 每个派发请求先核实目标仓库身份、工作流、业务日期、任务唯一键、账户用量和 Windows Runner 的在线/繁忙状态。
- 额度使用量与剩余额度不同：没有可靠余额证明时输出 `unknown`；不能把 API 失败当成免费额度耗尽。
- 同一任务只能有一个活跃执行或已核验产物。失败补偿必须先检查实际副作用和上游数据版本。

## 当前状态

**目前已部署：云端只读预检 + 5 分钟云端只读任务复核。** `scheduler.yml` 支持手动按需预检；`reconcile.yml` 按 5 分钟间隔复核 `state/tasks.json` 中已登记的任务（当前为空）。二者均不派发、不取消、不写入账本、不发布 APK。业务仓库的原生产定时源尚未迁移，避免双触发。

当前凭据与权限边界：

- `TEST_ACTIONS_READ_TOKEN`：Test 私有仓库 Actions 读写；`CHAONPU_ACTIONS_READ_TOKEN`：仅 Stock 与 ziwu 签名私有仓库 Actions 读写。两者虽然名称含 READ，但已验证具有私有仓库的 `workflow_dispatch` 写入能力。**不授权公开 `chaonpu/ziwuxilv`**。
- `TEST_BILLING_READ_TOKEN`、`CHAONPU_BILLING_READ_TOKEN`：仅用于对应个人账户 Billing Usage GET。
- `TEST_RUNNER_READ_TOKEN`、`RUNNER_READ_TOKEN`：仅用于目标仓库的 Runner 状态 GET。

`ZIWUXILV/database` 当前仅参与 Stock 的私有数据依赖，不由调度中心派发工作流，因此不要求该组织的 Billing、Actions 或 Runner 令牌。

目标阶段：安全完成 Stock 单任务演练后，再逐项串联 Test、Android 私有签名。公开正式版由 `chaonpu/ziwuxilv` 自己的 `publish-android-release.yml` 负责发布，在私有签名产物经核验后通过独立发布请求触发，**不使用 Runner 的私有仓库令牌跨仓库派发公开发布工作流**。详见 `docs/deployment.md`。

## 使用入口

- [云端按需预检（手动）](https://github.com/chaonpu/Runner/actions/workflows/scheduler.yml)
- [Stock 持久化防重派发演练（手动，仅无操作 canary）](https://github.com/chaonpu/Runner/actions/workflows/stock-noop-pilot.yml)
- [云端待执行任务复核（每 5 分钟）](https://github.com/chaonpu/Runner/actions/workflows/reconcile.yml)
- [经批准签名 APK 公开暂存（手动，Pre-release）](https://github.com/chaonpu/Runner/actions/workflows/stage-android-apk.yml)
- [安全单元测试](https://github.com/chaonpu/Runner/actions/workflows/validate.yml)
- [完整部署状态与授权要求](docs/deployment.md)

**正式策略／构建派发尚未启用**：已有的 `TEST_ACTIONS_READ_TOKEN`、`CHAONPU_ACTIONS_READ_TOKEN` 虽名称含 READ，但用户确认它们具备 Actions:write，已在 Stock 和 Test 两次真实无业务副作用 canary 中验证成功。仍需业务工作流的任务编号、核验、幂等适配，以及可靠额度判断。

## APK 暂存与正式发布分离

新 APK 经 `Test` 完整验证和 `ziwuxilv-android` 私有生产签名后，管理员可以使用版本专用批准口令手动将**签名 APK 和校验清单**存放到 `Runner` 的公开预发布 Release，创建匿名 HTTPS 下载地址。需要新版本、精确的 Test/签名 Run、源码 SHA、生产证书及 APK 哈希核验。**此操作会提前公开 APK，请勿在未获准时触发。**

这不更新用户 APP：正式版 `chaonpu/ziwuxilv` 仍须独立审批和发布请求。流程详见 [完整部署说明](docs/deployment.md)。历史 v2.3.4 不会自动重新暂存或发布。

## 私有 Stock 防重复派发演练

`.github/workflows/stock-noop-pilot.yml` **只允许 `chaonpu` 从 `main` 手动启动**。填写测试日期（YYYY-MM-DD），先将 `pilot:stock:日期` 原子登记到公开的 `state/pilot_runs.json`，然后仅派发私有 `chaonpu/Stock/.github/workflows/runner-dispatch-canary.yml`（无源码检出、无策略、无 database 操作、无邮件）。返回的实际 Run ID 继续写入同一登记文件；每 5 分钟的 `reconcile.yml` 通过已有只读令牌查询其实际状态，**不自动重试或执行策略**。

同一日期的再次请求会停止，不会盲目重复派发；写入响应未知时需人工检查登记表和运行页面。测试日期只是公开演练标签，**不是交易日期计算结果**。

正式策略的 `dispatchEnabled` 和 `enabledForDispatch` 仍全部关闭。不得把 canary Success 当成正式账本已入账。
