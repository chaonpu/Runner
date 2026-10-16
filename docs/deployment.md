# 统一调度中心部署与迁移计划（2026-10-17）

## 实际部署

- [x] 公开 `chaonpu/Runner` 初始化，保留通用调度代码，**不保存任何私有源码**。
- [x] 已映射五个业务仓库以及 **三个相互独立的计费所有者**：`anwpustudent-lab`、`chaonpu`、`ZIWUXILV`。
- [x] 仅手动运行的云端 preflight 工作流 `.github/workflows/scheduler.yml`。
- [x] Cloud Ubuntu 的纯路由单元测试与实盘只读接口测试。
- [x] 利用截图所示的六个 Repository Secrets 读取个人 Billing Usage、Actions 运行、Windows Runner 状态；没有真实派发。
- [ ] 组织 `ZIWUXILV` 的 Billing Usage、Runner/Actions 只读权限。
- [ ] 各所有者的跨仓库 Actions:write 凭据与授权试验。
- [ ] 将 Stock A/US/ETF 工作流改为可核实任务标识、按交易日去重，并适配 Windows 本地版本。
- [ ] 正式业务入口与完成核验串联：Stock→database、Test→签名→公开发布。
- [ ] 原仓库的外部计划/原定时来源核实并分批切换，防止双触发；不要直接停止目前生产策略。
- [ ] 支持原子更新和隐私最小化的任务登记，恢复/补算，仅在实测完成后开启每 5 分钟的未完成任务复核。

## 运行方式

在公开仓库 Actions 中运行 **Unified on-demand build and strategy preflight**，填写任务：
- `stock_a` / `stock_us` / `stock_etf`：填写 `business_date`（YYYY-MM-DD）。
- `android`：填写 `source_sha`（Test/main 的完整 40 位提交）。
- `database_sync`：填写 `source_version`；目前仅提示缺乏具体执行合同。
- `apk_publish`：填写 `source_version`；目前仅执行规则验证，不上传。

Scheduler 优先使用 GitHub-hosted Ubuntu 运行预检，因此 Windows 关机时也能**预检、记录暂不可用的原因**。但如果没有有效执行资源，它不能凭空完成策略计算或 APK 签名。

## 云端计费口径

Billing Usage Summary 返回 `grossQuantity`、`discountQuantity` 及按 SKU 的用量，但不能单独推导精确实时剩余额度。Linux SKU 用量不等于通用可用额度，组织和个人额度也不能混用。

只有可靠的账户免费包含额度/预算状态与当前任务用量校验完成后，才可标记 `independentlyVerified=true` 的 `available/exhausted`。目前真实 API 输出 `unknown`。

当状态为 `unknown`，仅在目标仓库已经有经过测试的 Windows 工作流、Runner 在线空闲且标签正确时，允许推荐本地路径；但**不直接派发**。任何无效凭据、Runner 403、网络故障或进度未知都会保持待处理。

## 三个 Owner 的权限

现有截图中的六项只读 Secret：
- `TEST_BILLING_READ_TOKEN`, `TEST_ACTIONS_READ_TOKEN`, `TEST_RUNNER_READ_TOKEN`；
- `CHAONPU_BILLING_READ_TOKEN`, `CHAONPU_ACTIONS_READ_TOKEN`, `RUNNER_READ_TOKEN`。

待增加：组织 `ZIWUXILV` 自身有权限的相应只读 Secret（名称写在 `config/repositories.json`）；需要真正派发时另配三个**严格分属所有者的** `Actions:write` 调度凭据。公开调度中心的 Actions 仓库 secrets 仅允许受信任的 `main` 分支手动/计划工作流使用，**不得暴露给 PR、公开 Issue、第三方脚本或构建产物**。发布仓库额外副作用需独立授权。

## 旧方案迁移边界

此前签名仓库内的 hourly observer 已在前期改为按需。本方案迁移之后，**旧 observer 及其测试工作流退役**；保留已经真实验收的 Test Windows CI / Windows 私有签名交接，以及现在仍用于正式发布的流程，不删除正式签名证书约束、账本策略或策略来源。

验证身份与防重优先于触发：业务运行的 `workflow_dispatch` 若无 `task_id` 输入，必须先补充任务标识并检查日志/目标产物一致性；`workflow_run_id` 可用于跟踪，但绿色状态不证明账本入账或发布成功。

## 运行器兼容

Stock 当前的 A/US 工作流是 `ubuntu-latest` 上的 Bash、Node、Python 组合，不能因为 `ziwuxilv-stock-win` 在线就直接换成 Windows 标签。Android 当前已实测不同的 Windows 构建和私有签名 Runner，第三层必须严格串行完成双方的提交与产物核验。

公众可以看到调度代码、仓库标识和测试结果，但不能通过 Runner 获得私有仓库内容、策略明细或签名材料。
