# 统一调度中心部署与迁移计划（2026-10-17）
## 2026-10-17 实测进度

- GitHub hosted `Runner scheduler safety tests`：Run `38946066842`，21 项路由与业务副作用安全测试通过。
- GitHub hosted `Coordinator cloud preflight live smoke`：Run `38945358576`，实际读取 Stock/Android Actions 状态、两个个人账户 Billing Usage、Test 与签名 Windows Runner；不调用任何私有仓库的 GET Contents、下载或 POST。一次性测试 workflow 已移除，结果仍可从 Runs 查证。
- GitHub hosted `Reconcile dispatched tasks (cloud, monitor only)`：Run `38946240769`，空注册表的只读状态复核通过。保留每 5 分钟 cron / 手动入口；测试用 push 触发已去除。
- 原私有签名仓库 `android-route-observer.yml` 及其相关四个旧工作流与三份探针脚本已删除；公开 APP 仓库旧的 `android-build-route-tests.yml` 和 `tools/build_route_policy.py` 已删除。
- 已验证过的 `Test` Windows 构建、签名仓库 Linux/Windows 正式签名，以及公开 APK 发布工作流均保留；**未修改策略、账本或最新正式版资产**。

目前 `state/tasks.json` 的 `tasks` 数组为空，因此 5 分钟复核只会验证当前队列，**没有实际任务被派发**。真正的 `dispatched/running/verified` 登记、冲突检测与恢复提交机制需要 `Runner` 自身任务登记写入及目标仓库的 Actions 写入授权完成后才能启用。

### 下一步所需权限（均为最小权限，令牌绝不存储在 Git）

当前只有六个只读 Secret。对每个跨账户业务 Owner 还需要 **Actions:write** 的单独授权：

- `TEST_ACTIONS_WRITE_TOKEN`：由 `anwpustudent-lab` 所有，唯一选中 `Test`。
- `CHAONPU_ACTIONS_WRITE_TOKEN`：由 `chaonpu` 所有，只选中要调度的 `Stock`、`ziwuxilv-android`、`ziwuxilv`。
- `ZIWUXILV_ACTIONS_WRITE_TOKEN`：`ZIWUXILV` 组织授权，只选中 `database`；该组织可能需要管理员审批。

还需要单独的 `ZIWUXILV_BILLING_READ_TOKEN`、`ZIWUXILV_ACTIONS_READ_TOKEN` 和 `ZIWUXILV_RUNNER_READ_TOKEN`（只在这些接口确实必要时授权；组织 Runner 是 `Self-hosted runners:read`，仓库 Runner 是 `Administration:read`）。**不要将这些令牌值发在聊天里。**

即便拥有 Actions:write，仍需完成每种任务的输入校验、账本去重、产物验收和可信额度/预算判定，不能直接打开全部自动派发。


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
