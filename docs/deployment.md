# 统一调度中心部署与迁移计划（2026-10-17）
## 2026-10-17 公开发布边界纠正

**用户明确指定 `CHAONPU_ACTIONS_READ_TOKEN` 仅覆盖私有仓库**，这一最小授权不需要扩大。此前尝试从 Runner 直接触发公开 `chaonpu/ziwuxilv` 的无业务 canary 返回 403 是**预期的权限隔离效果**，不能归类为待修复的 PAT 问题。

调度路由重新划分：
- `chaonpu/Runner` 使用现有私有令牌只向 `chaonpu/Stock`、`anwpustudent-lab/Test` 和 `chaonpu/ziwuxilv-android` 派发**已批准的私有工作流**；
- `chaonpu/ziwuxilv` **不再是 Runner 的 dispatch 目标**；发布仓库原有 `.github/workflows/publish-android-release.yml` 由本仓库自身 `GITHUB_TOKEN` 配合 `contents:write` 执行发布，接受被严格校验的 `android-publish-request.json` 更新或仓库内手动启动；
- 私有签名成功**不能直接构成**公开发布许可。必须以独立身份核对签名 APK 的证书、版本、源 SHA、artifact hash，结合发布请求与现有版本约束后，才由公开仓库完成最终发布。暂不自动把私有 signed Artifact 推送到公开仓库，也不添加跨公开仓库写入 PAT。

已从 Runner 的可派发任务列表移除 `apk_publish`，也删除了私有写令牌对公开仓库的 canary 路由和入口。新旧工作流此阶段不会因此自动启动业务或发布 APK。

## 2026-10-17 跨仓库真实权限与 Windows Stock 验收

仅对固定的 `runner-dispatch-canary.yml` 发起过 `workflow_dispatch`；这些测试 workflow 不检出私有代码、数据库和签名密钥：

| 目标 | 调度 Run / 目标 Run | 结果 |
|---|---|---|
| `chaonpu/Stock` | `38948088743` → `38948101845` | Success |
| `anwpustudent-lab/Test` | `38948088743` → `38948104668` | Success |
| `chaonpu/ziwuxilv-android` | `38950285244` → `38950299547` | Success |
| `chaonpu/ziwuxilv` | `38950285244` | HTTP 403：**预期隔离**，不是私有令牌缺陷；不再作为 Runner 目标 |

因此 `TEST_ACTIONS_READ_TOKEN` 对 Test、`CHAONPU_ACTIONS_READ_TOKEN` 对 Stock 和签名仓库已实测具有工作流派发能力。**CHAONPU 令牌只覆盖私有业务仓库是预期的安全边界**，不扩大 Fine-grained PAT Repository access。公开版发布沿用公开仓库现有验证与仓库自身发布工作流，作为独立、受控的下游阶段。

公共调度仓库的权限测试 workflow 已调为 **manual-only**，避免错误重试。真实 A/US 策略、APK 签名和正式发布未被此次派发实验运行。

Stock Windows 的工作流 `stock-windows-capability.yml`：Run `38948966616` 已证明 Node 22、npm、Python 3.14、Git 及 108GB 工作盘可用；Run `38949149394` 成功检出私有 Stock、完成契约检查并通过 **18 个只读 Node 账本单元测试**。Windows 上 Python 3.13 的 `setup-python` 系统工具缓存安装会因权限问题失败，暂使用已有 Python 3.14；完整策略的 Python 包和 Windows 移植仍未验收。GitHub Checkout 的网络偶发失败已在*仅诊断工作流*中增加超时和一次 HTTP/1.1 重试，可能仍受网络影响。诊断工作流现已恢复为仅手动触发。

A 股与美股正式工作流新增*可选* `task_id` 输入校验（`stock_a:YYYY-MM-DD`、`stock_us:YYYY-MM-DD`），不改变策略或账本运算，也**不表示已实现业务幂等**；所有正式派发开关仍为 `false`。

## 2026-10-17 实测进度

- GitHub hosted `Runner scheduler safety tests`：Run `38946066842`，21 项路由与业务副作用安全测试通过。
- GitHub hosted `Coordinator cloud preflight live smoke`：Run `38945358576`，实际读取 Stock/Android Actions 状态、两个个人账户 Billing Usage、Test 与签名 Windows Runner；不调用任何私有仓库的 GET Contents、下载或 POST。一次性测试 workflow 已移除，结果仍可从 Runs 查证。
- GitHub hosted `Reconcile dispatched tasks (cloud, monitor only)`：Run `38946240769`，空注册表的只读状态复核通过。保留每 5 分钟 cron / 手动入口；测试用 push 触发已去除。
- 原私有签名仓库 `android-route-observer.yml` 及其相关四个旧工作流与三份探针脚本已删除；公开 APP 仓库旧的 `android-build-route-tests.yml` 和 `tools/build_route_policy.py` 已删除。
- 已验证过的 `Test` Windows 构建、签名仓库 Linux/Windows 正式签名，以及公开 APK 发布工作流均保留；**未修改策略、账本或最新正式版资产**。

目前 `state/tasks.json` 的 `tasks` 数组为空，因此 5 分钟复核只会验证当前队列，**没有实际任务被派发**。真正的 `dispatched/running/verified` 登记、冲突检测与恢复提交机制需要 `Runner` 自身任务登记写入及目标仓库的 Actions 写入授权完成后才能启用。

### 跨仓库 Actions 派发权限已经实际验证（2026-10-17）

用户确认已有的 `TEST_ACTIONS_READ_TOKEN` 和 `CHAONPU_ACTIONS_READ_TOKEN` **本身具备 Actions:write**，无需新建 `*_WRITE_TOKEN`。虽然 Secret 名称含 `READ`，实际权限来自令牌的 GitHub 权限设置，必须将它们按**可写敏感凭据**保护。

从公开调度中心真实派发了两个**无业务副作用**的 canary：

- Stock：`chaonpu/Stock` Run `38948101845`，成功；
- Test：`anwpustudent-lab/Test` Run `38948104668`，成功；
- 发起端：`chaonpu/Runner` Run `38948088743`，API 均返回真实 Run ID。

两次私有运行只验证任务 ID 与 Windows Runner，不下载源码、不访问策略数据、不签名、不发布。公开端权限测试 `.github/workflows/dispatch-canary.yml` 已改为**手动触发**，避免重复。

`ZIWUXILV/database` 目前**没有需要中心触发的工作流**，不要求额外令牌，也已从 `config/repositories.json` 可派发任务列表移除；仍是 Stock 的私有数据依赖。后续只有在实际需要组织 workflow_dispatch 时才单独评估授权。

实际策略派发仍保持关闭：各业务工作流必须先增加任务 ID、幂等去重、Windows 执行适配、产物验收与可信额度判断，再允许云端调度器调用。


## 实际部署

- [x] 公开 `chaonpu/Runner` 初始化，保留通用调度代码，**不保存任何私有源码**。
- [x] 映射五仓库依赖，但中心目前只调度 `anwpustudent-lab` / `chaonpu` 下的工作流，`ZIWUXILV` 暂不需要令牌。
- [x] 仅手动运行的云端 preflight 工作流 `.github/workflows/scheduler.yml`。
- [x] Cloud Ubuntu 的纯路由单元测试与实盘只读接口测试。
- [x] 利用截图所示的六个 Repository Secrets 读取个人 Billing Usage、Actions 运行、Windows Runner 状态；没有真实派发。
- [x] 确认 ZIWUXILV 目前无需工作流，已撤销额外令牌需求。
- [x] 两个现有 Actions 令牌已完成 Stock 与 Test 的真实跨仓库 no-op 派发试验。
- [ ] 将 Stock A/US/ETF 工作流改为可核实任务标识、按交易日去重，并适配 Windows 本地版本。
- [ ] 正式业务入口与完成核验串联：Stock→database、Test→签名→公开发布。
- [ ] 原仓库的外部计划/原定时来源核实并分批切换，防止双触发；不要直接停止目前生产策略。
- [ ] 支持原子更新和隐私最小化的任务登记，恢复/补算，仅在实测完成后开启每 5 分钟的未完成任务复核。

## 运行方式

在公开仓库 Actions 中运行 **Unified on-demand build and strategy preflight**，填写任务：
- `stock_a` / `stock_us` / `stock_etf`：填写 `business_date`（YYYY-MM-DD）。
- `android`：填写 `source_sha`（Test/main 的完整 40 位提交）。
- `database_sync`：填写 `source_version`；目前仅提示缺乏具体执行合同。
- 公开 APK 发布**不在 Runner 的可派发任务列表中**；仅由公开仓库自身的验证后发布工作流处理。

Scheduler 优先使用 GitHub-hosted Ubuntu 运行预检，因此 Windows 关机时也能**预检、记录暂不可用的原因**。但如果没有有效执行资源，它不能凭空完成策略计算或 APK 签名。

## 云端计费口径

Billing Usage Summary 返回 `grossQuantity`、`discountQuantity` 及按 SKU 的用量，但不能单独推导精确实时剩余额度。Linux SKU 用量不等于通用可用额度，组织和个人额度也不能混用。

只有可靠的账户免费包含额度/预算状态与当前任务用量校验完成后，才可标记 `independentlyVerified=true` 的 `available/exhausted`。目前真实 API 输出 `unknown`。

当状态为 `unknown`，仅在目标仓库已经有经过测试的 Windows 工作流、Runner 在线空闲且标签正确时，允许推荐本地路径；但**不直接派发**。任何无效凭据、Runner 403、网络故障或进度未知都会保持待处理。

## 私有所有者的权限

现有截图中的六项只读 Secret：
- `TEST_BILLING_READ_TOKEN`, `TEST_ACTIONS_READ_TOKEN`, `TEST_RUNNER_READ_TOKEN`；
- `CHAONPU_BILLING_READ_TOKEN`, `CHAONPU_ACTIONS_READ_TOKEN`, `RUNNER_READ_TOKEN`。

当前没有新增令牌需求。已验证的两枚 Actions 令牌具备写入授权，但必须仅用于白名单工作流，并按敏感令牌保护。公开调度中心的 Actions Secrets 只能由受信任的 `main` 分支手动/计划工作流使用，**不得暴露给 PR、公开 Issue、第三方脚本或构建产物**。正式发布仍需额外产物核验和独立的发布门禁。

## 旧方案迁移边界

此前签名仓库内的 hourly observer 已在前期改为按需。本方案迁移之后，**旧 observer 及其测试工作流退役**；保留已经真实验收的 Test Windows CI / Windows 私有签名交接，以及现在仍用于正式发布的流程，不删除正式签名证书约束、账本策略或策略来源。

验证身份与防重优先于触发：业务运行的 `workflow_dispatch` 若无 `task_id` 输入，必须先补充任务标识并检查日志/目标产物一致性；`workflow_run_id` 可用于跟踪，但绿色状态不证明账本入账或发布成功。

## 运行器兼容

Stock 当前的 A/US 工作流是 `ubuntu-latest` 上的 Bash、Node、Python 组合，不能因为 `ziwuxilv-stock-win` 在线就直接换成 Windows 标签。Android 当前已实测不同的 Windows 构建和私有签名 Runner，第三层必须严格串行完成双方的提交与产物核验。

公众可以看到调度代码、仓库标识和测试结果，但不能通过 Runner 获得私有仓库内容、策略明细或签名材料。

## 2026-10-17 新增：经批准的 APK 公开暂存（真实发布仍独立）

新增 `.github/workflows/stage-android-apk.yml` 和 `scheduler/apk_stage.py`，运行于公开 Runner 的 GitHub-hosted Ubuntu、**仅手动触发**；不从公开仓库克隆私有 Test 或 Stock 源码。

手动输入：`signing_run_id`、`test_run_id`、`source_commit`、`version_name`、`version_code`、`public_staging_approval`。批准口令必须逐字为 `STAGE-PUBLIC-APK-<version_name>-<signing_run_id>`，仅 `chaonpu` 账户、`main` 分支允许执行。批准意味着**签名 APK 将通过公开 Pre-release 提前对所有人可下载**；不等于正式版发布批准。

签名仓库私有 Run 必须是 `windows-signing-verified-handoff.yml` 的 `workflow_dispatch` 成功执行，且其私有 Artifact 名称恰为 `windows-signed-release`，内含从**新版本签名流程**生成的 `publish-candidate-metadata.json`。历史 v2.3.4 Artifact 缺少此清单，且与现有正式 versionCode 117 相等，将被拒绝。

云端暂存工作流采用此前已验证的两个私有 Actions 令牌 **只读** 查询/下载：
- `CHAONPU_ACTIONS_READ_TOKEN` 仅读私有签名 Run 和签名 Artifact；
- `TEST_ACTIONS_READ_TOKEN` 仅读 Test Run 的原始提交/成功状态；
- `Runner` 仓库自身的 `GITHUB_TOKEN`，`contents:write` **仅创建本仓库公开 Pre-release**。不扩展私有令牌到 `chaonpu/ziwuxilv`。

内部校验：Test/签名 Run ID、原仓库、指定工作流、Test 允许 `push` 或 `workflow_dispatch`，签名工作流仅允许 `workflow_dispatch`、`main`、`completed/success`、源 SHA、候选 metadata 的精确字段、APK/侧边 SHA256、证书、包名与版本；目录文件白名单不允许 `release-source.zip`、密钥或其他新增文件。另对当前 `chaonpu/ziwuxilv/android-native/version.json` 做在线 versionCode 检查，版本不高于正式版、相同版本已在 Runner 暂存都拒绝。

允许上传到 `chaonpu/Runner` 公开 Release 的资产只有：
1. `ziwuxilv-verified-v<version>.zip`（内含正式签名 APK 和最少的验证文件）；
2. `staging-receipt.json`（公开源 URL、ZIP/APK SHA、Test/Signer Run、源码 SHA、版本和证书）。

Release tag `staged-apk-v<version>-signrun-<runId>`，`--prerelease`；现有相同 Tag 或版本不覆盖。创建后验证匿名 ZIP 下载 SHA256。**网络/上传响应不确定时不自动重试**，需先人工核查是否已经创建公开资源。公开暂存 ZIP 可用于将来的 `ziwuxilv/android-publish-request.json`，但本阶段不会自动提交正式发布请求。

演练记录：`Runner scheduler safety tests` Run `38954953009` **39 项通过**。真正的跨公开 APK 上传及公开 Release 创建操作**尚未进行**；用户同意的是经审批的未来新 APK 暂存，而不是重发 v2.3.4 或直接推送用户更新。


## 2026-10-17 Stock Windows 完整依赖及离线策略回归验收

`Stock` Windows Run [38958263612](https://github.com/chaonpu/Stock/actions/runs/38958263612) **Success**，通过 Python 正式依赖完整安装、43 项 A/美股及账本回归、18 项 Node 账本测试、10 个账本契约文件、Git Bash 与 Windows 工具链校验。期间修复了回归测试默认 GBK 读取 UTF-8 报告问题；并仅在 Job 环境为 Runner 添加 Git Bash PATH，未修改生产策略运算。

`chaonpu/Stock/.github/workflows/run-a-share.yml` 与 `run-us.yml` 现均允许显式 `runner_mode=windows`，但默认仍为 `ubuntu`；Windows 必须传正确 `task_id`，运行时使用 Git Bash 和隔离的系统 Python 3.14 venv，随后执行原业务步骤。**尚未受控实跑真实行情、策略调仓和 database 读写**，因此本项目 `config/repositories.json` 对这两个市场仍保留 `windowsWorkflow=null`，任务自动派发继续关闭。不能因为离线回归 Success 就自动切换真实策略。

私有 [Windows 验收说明](https://github.com/chaonpu/Stock/blob/main/docs/windows-runner-validation.md) 记录了后续实际数据源验证、连接失败恢复与任务幂等门禁。诊断流程已改为仅手动触发，不额外安排重复运行。

