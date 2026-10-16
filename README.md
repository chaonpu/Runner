# 子午汐律 · GitHub Actions 统一调度中心

本公开仓库 **只保存通用调度程序、明确允许公开的仓库/工作流标识以及不涉及业务隐私的状态**。

- **不**检出、下载、上传或复制 `Test`、`Stock`、`database` 的私有源码、仓位、账本或 APK 签名密钥。
- **不**在调度日志或 Git 中保存个人访问令牌、策略明细、交易记录、完整私有工作流日志。
- GitHub 托管 Ubuntu 负责云端调度；Windows 的自托管 Runner 只在其目标私有仓库中执行。
- 每个派发请求先核实目标仓库身份、工作流、业务日期、任务唯一键、账户用量和 Windows Runner 的在线/繁忙状态。
- 额度使用量与剩余额度不同：没有可靠余额证明时输出 `unknown`；不能把 API 失败当成免费额度耗尽。
- 同一任务只能有一个活跃执行或已核验产物。失败补偿必须先检查实际副作用和上游数据版本。

## 当前状态

**阶段 1：只读云端调度基座。** 公开仓库中的 Scheduler 默认为 `dry-run`，不调用工作流派发、取消、代码写入或 APK 发布 API。新旧业务工作流的定时触发尚未迁移，避免在未确认去重之前重复派发。

初始凭据名称：

- `TEST_ACTIONS_READ_TOKEN`、`CHAONPU_ACTIONS_READ_TOKEN`：仅用于 Actions 运行状态读取。
- `TEST_BILLING_READ_TOKEN`、`CHAONPU_BILLING_READ_TOKEN`：仅用于对应个人账户 Billing Usage GET。
- `TEST_RUNNER_READ_TOKEN`、`RUNNER_READ_TOKEN`：仅用于目标仓库的 Runner 状态 GET。

组织 `ZIWUXILV` 的 Billing/Runner 访问需要单独授权。任何未配置或权限不足都记录 `unknown`，不使用另一所有者的额度冒充。

目标阶段：安全完成 Stock 单任务演练后，再逐项迁移 database、Test、Android 私有签名与公开正式发布；所有切换都需明确定义输出验证和幂等机制。详见 `docs/deployment.md`。
