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

初始凭据名称：

- `TEST_ACTIONS_READ_TOKEN`、`CHAONPU_ACTIONS_READ_TOKEN`：仅用于 Actions 运行状态读取。
- `TEST_BILLING_READ_TOKEN`、`CHAONPU_BILLING_READ_TOKEN`：仅用于对应个人账户 Billing Usage GET。
- `TEST_RUNNER_READ_TOKEN`、`RUNNER_READ_TOKEN`：仅用于目标仓库的 Runner 状态 GET。

组织 `ZIWUXILV` 的 Billing/Runner 访问需要单独授权。任何未配置或权限不足都记录 `unknown`，不使用另一所有者的额度冒充。

目标阶段：安全完成 Stock 单任务演练后，再逐项迁移 database、Test、Android 私有签名与公开正式发布；所有切换都需明确定义输出验证和幂等机制。详见 `docs/deployment.md`。

## 使用入口

- [云端按需预检（手动）](https://github.com/chaonpu/Runner/actions/workflows/scheduler.yml)
- [云端待执行任务复核（每 5 分钟）](https://github.com/chaonpu/Runner/actions/workflows/reconcile.yml)
- [安全单元测试](https://github.com/chaonpu/Runner/actions/workflows/validate.yml)
- [完整部署状态与授权要求](docs/deployment.md)

**正式派发尚未启用**：还需三所有者的独立 Actions:write 凭据、ZIWUXILV 的只读权限和业务工作流中的任务标识、核验与幂等适配。
