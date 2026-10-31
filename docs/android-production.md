# Android 私有生产构建与签名链（2026-10-17）

## 生产入口

[RUNNER 手动受控入口](https://github.com/chaonpu/Runner/actions/workflows/android-production.yml)

- 默认 `execute=false`：读取 Test/main 精确 SHA 和当前登记，不执行私有派发。
- `execute=true`：仅 `chaonpu` 可启动。需 Test 与签名两个独立 Windows Runner 在线且空闲，相关私有工作流无其他活跃运行。
- `source_sha` 留空：仅当既有凭据可读取 Test/main SHA 时自动选择当前提交。目前 Test Actions/Runner/Billing 令牌在该端点仍缺少 `Contents: Read`，只读预检会报告 `manualSourceShaRequired=true`；**不能在 SHA 为空时执行生产构建**。
- `source_sha` 填写完整 40 位 SHA：当源分支 SHA 无法读取时，以人工确认的 SHA 建立唯一预约；在派发后必须校验 GitHub 返回的**原始 Test Run 的 `head_sha` 等于人工指定值**，且 Run 属于 main 的指定工作流。否则立即停止，不进入签名。可从私有 [Test/main](https://github.com/anwpustudent-lab/Test/commits/main) 复制当前最新 SHA（提交可能随时间变化）。
- `resume=true`：必须提供登记的原始 `source_sha`，默认仅继续核验原始 Test Run 或原始签名 Run，不重新发送已派发的 Test。仅另行显式选择下述 `recover_live_validation` 且全部边界核验通过时，允许一次完整重建。若已在 POST 后发生未确认的网络错误或尚未登记 Run ID，必须人工核对，不能自动重派。
- 同一源码 SHA 的构建身份只保留一项公开状态登记；已签名成功的 SHA 不自动重复构建。

## 安全派发顺序

1. 从私有 Test 的 `main` 元数据获取当前 SHA，只保存提交哈希。
2. 检查两个独立 Windows 服务及私有工作流活跃状态；在 `state/android-production.json` 以 GitHub Contents SHA CAS 预约唯一 SHA。
3. 派发 `anwpustudent-lab/Test/.github/workflows/test-self-hosted.yml`，要求 GitHub API 返回实际 Run ID。
4. 同一 RUNNER Job 前台持续检查该 Run 的仓库、触发方式、分支、SHA、重试次数、最终成功状态和执行 Runner；验证存在 `windows-test-verified` 私有 handoff artifact。
5. 原 Test Run 成功且签名 Runner 在线后，**先 CAS 持久登记 `signer_reserved` 派发意图，再**派发 `chaonpu/ziwuxilv-android/.github/workflows/windows-signing-verified-handoff.yml`；仅传入原 Test Run ID 与源码 SHA。若 POST 响应不明或 Run ID 登记失败，原状态禁止自动重派签名。
6. 签名仓库在私有 Runner 上校验完整源码/unsigned APK SHA256、测试与 lint、固定生产证书指纹、包名与版本，完成签名；密钥始终留在私有工作区。
7. RUNNER 核验原始签名 Run 成功、实际 Runner 匹配，并确认存在非空 `windows-signed-release` 私有 artifact 后，登记 `signed_verified`。

公开 RUNNER 不读取私有源码、私有产物、签名密钥、完整构建日志，也不运行 Test 的业务源码。

## 停止与恢复

- 源码已预约，但 GitHub POST 返回不确定：原预约保留；不自动重试，先从私有仓库核实是否已经开始运行。
- Test 有精确 Run ID 但执行中断：手动 `resume=true` + 原 SHA，继续监控同一 Run，禁止新建 Test Run。
- 在线验证已失败且已人工复核私有日志时，可显式设置 `execute=true`、`resume=true`、`recover_live_validation=true` 和原始 SHA，申请**仅一次完整重建**。默认续跑不会重派。仅限 Windows、原任务已请求实时验证、首次 Test 的源码/Release 编译/版本及日历验证通过、失败步骤恰为单元/静态检查组及实时证据验证、handoff 全部跳过、没有 Artifact、尚未预约签名；只读步骤元数据不被冒充为网络故障证明。恢复前 CAS 预约并保留原失败 Run 和分类；任何预约/POST 不确定都停止。新 Test 仍必须为 main、原始 SHA、首次尝试、受信任 Runner，重新通过全部单元测试、Lint、Debug、实时验证及 handoff 检查后才允许签名。它与既有源码下载恢复合计最多一次；不复用失败 APK、不接受 GitHub Re-run 的第二次尝试、不自动发布。
- Test 已成功，签名机离线：保留 Test Run ID，待签名机重新在线后按原 SHA 手动恢复；须在私有 Artifact 有效期内完成。
- 已进入 `signer_reserved` 而未登记签名 Run ID：必须人工查看签名仓库有无原始派发及副作用；调度器不接受自动恢复重派。
- 签名有 Run ID 但状态不明：仅观察原始签名 Run，不能重复签名。
- 签名步骤失败：停止并保留记录，人工检查私有日志后才能决定新源码或显式恢复方案。
- 同一 Windows 签名 Run 的第 2 次尝试已经成功时，可在原「APK 构建与签名（手动）」选择 `execute=true`、`resume=true` 并填写原始源码 SHA。调度器必须证明首次仅在 HTTPS Git 网络预检失败、交接包读取及所有密钥/签名/上传步骤均跳过，第二次在原签名 Runner 完成全部校验和上传，且源码策略 SHA 与 Run ID 一致；同时重新验证原 Test、实时行情及私有 Artifact。该恢复只读取证据，不派发新 Test 或签名任务。
- 上述恢复不接受第三次尝试、云端签名重试、未完成任务或首次已经进入签名的情况。恢复检查完成前不更改 `signed_verified`；之后仍需在原「APK 发布（手动）」选择「正式」，由发布流程重新核验 APK 字节、证书及版本。
- 本入口无定时五分钟轮询；一旦启动，同一公开 Job 前台监控至完成或保守停止。

## 公开 APK 发布边界

本入口完成后只获得**私有签名成功**，并不等于正式 APK 已发布。若要公开暂存，另行使用 [签名 APK 独立审批暂存](https://github.com/chaonpu/Runner/actions/workflows/stage-android-apk.yml)，按其要求提供来源 SHA、Test Run ID、签名 Run ID、版本及人工审批短语。正式发布继续使用 `chaonpu/ziwuxilv` 自有发布保护流程，必须经过现有版本递增与更新清单校验。未经审批，绝不自动发布。

## 2026-10-17 正式接入验收结果

- **私有 Android 构建、测试及签名生产链已完成端到端验收**。源码 SHA：`ce0be22bf758f2621235a165bfc07c059cca5de1`；APP 版本：`2.3.4 (117)`。
- Test Run [39039852886](https://github.com/anwpustudent-lab/Test/actions/runs/39039852886) 成功：Windows SDK/JDK、源码校验、Unsigned Release、单元测试、Lint、Debug、通知 Manifest 和私有 `windows-test-verified` Artifact 均成功。
- 原签名 Run [39041021094](https://github.com/chaonpu/ziwuxilv-android/actions/runs/39041021094) 在官方上传组件下载中断时安全停止；第二次 [39043072529](https://github.com/chaonpu/ziwuxilv-android/actions/runs/39043072529) 在下载恢复回归测试中安全停止，二者均未进入密钥恢复或 APK 签名。
- 签名恢复 Run [39043329939](https://github.com/chaonpu/ziwuxilv-android/actions/runs/39043329939) **success**。源码、原 Test Run、Unsigned APK SHA256、签名证书、包名、版本、APK SHA256、发布候选元数据及 `windows-signed-release` 私有 Artifact 上传全部通过。签名 Artifact ID：`12105485401`，GitHub Artifact SHA256：`977f8c747a41bb8302dd5fc41054f5bf81badbd0298899821135dcf8dc5c31d6`。
- RUNNER [39043318075](https://github.com/chaonpu/Runner/actions/runs/39043318075) **success**，`state/android-production.json` 当前精确源码标记为 `signed_verified`，并保留先前两次失败 Run 的溯源。
- 新增独立的签名仅恢复入口 `android-signing-recovery-launch.yml`：仅显式批准、只允许旧签名任务在访问密钥前失败、必须原 Test Run 成功且原 Artifact 存在时受控启动；采用 CAS 派发意图并限制最多两次、禁止盲目重派。
- 本轮 Windows 同机签名 Runner 经真实测试可**只读复用 Test Runner 的官方公开上传组件缓存**，每次重新验证固定 Git Blob 哈希及长度；不复制签名密钥，也不公开源码。
- Android 构建启动和签名恢复两个一次性申请文件均为 `enabled=false`；未生成任何公开 Release，也未修改正式版更新清单。

## 云端额度优先与 Windows 安全回退（路由阶段）

- 新增 Test 私有 `android-cloud-verified.yml`，**仅 `workflow_dispatch`**、GitHub 标准 `ubuntu-24.04`、`contents: read`。按照已验收的 Test 口径运行版本检查、单元测试、Release Lint、Debug APK、Unsigned Release 和通知权限检查，私有产物为 `cloud-test-verified`（3 天有效），字段与已有 Windows `windows-test-verified` 一致。
- 旧版 `Test/.github/workflows/android.yml` 和 `ziwuxilv-android/.github/workflows/android.yml` 已关闭 `push`、PR 和定时自动派发入口，仅保留手动兼容通道。避免它们绕过 RUNNER 用量预检、重复运行私有云端编译或旧版签名，亦不改变现有正式发布工作流。

- 私有 Windows 签名工作流只认可精确 `main`、原始 `workflow_dispatch`、首次尝试且成功的 Test Run；根据 GitHub 原始 `path` 将受信任 Windows 路由绑定 `windows-test-verified`，将受信任云端路由绑定 `cloud-test-verified`。**不接受由调用者自由提供 Artifact 名称**，更不允许未验证源码直接签名。
- RUNNER `scheduler/android_route.py` 在正式派发前从 Test 所属账号读取当月 GitHub Actions 用量摘要及明细，只有“私有 Linux 标准 Runner 的历史用量可以可靠归属、公开仓库标准托管用量已排除、SKU 不存在未知倍率、当前剩余分钟**估算值** ≥ 标准云端构建超时 100 + 保留 200 + 报告滞后保护 400 = **700 分钟**”时才候选云端。低于门槛、权限不足、用量结构不完整或历史报告异常一律使用已验证的 Windows 构建。
- **GitHub 用量 API 不提供精确实时可用免费分钟证明**；`hostedQuotaGuaranteed=false`。该机制优先避免误用付费额度，不能保证云端 Job 启动后不会遇到 GitHub 平台的账单限制。云端 Job 一旦实际派发，不因普通编译失败或网络错误自动再派 Windows；需核验原 Run 后人工处理，防止双跑和多余消耗。
- 每次新源码只保留一个 `state/android-production.json` 条目，提前固化 `route`（`hosted` / `windows`），后续恢复继续检查原 SHA、工作流、Run ID、对应 Runner/Artifact。签名仍运行在**私有 Windows 签名 Runner**，不会上传 keystore。
- 2026-10-17 `Runner/validate.yml` [Run 39045824212](https://github.com/chaonpu/Runner/actions/runs/39045824212) 269 项安全测试通过，在线只读报告 `route=windows`、`routeReason=remaining_estimate_below_conservative_threshold`、两个 Windows Runner 在线且无活跃的相关工作流。
- **只完成云端契约和调度逻辑的静态/离线验收**；未在账号当前额度低于保护门槛时强行派发收费风险的私有 Ubuntu 编译任务。因此云端 Test → Windows 签名分支尚需在有足够免费额度时完成一次真实端到端验收，不能混同已经验证的 Windows → Windows 生产链。

## 当前限制
- 公开工作流能否实际派发取决于 RUNNER Secrets：`TEST_ACTIONS_READ_TOKEN`、`CHAONPU_ACTIONS_READ_TOKEN` 需 Actions 写与运行只读；`TEST_RUNNER_READ_TOKEN`、`RUNNER_READ_TOKEN` 已在实时只读预检中确认可分别读取两个 Windows Runner 的状态。可选 `TEST_SOURCE_READ_TOKEN` 只需精确限定私有 Test 仓库 `Contents: Read`，用于自动读取 `main` SHA；不配置时手动指定 SHA 即可保持源码权限隔离。某个必要权限缺失会保守停止。
- 本次 Windows 私有链已通过真实 Run 和 Artifact 验收；若后续换机器、更新工具或接入云端路径，仍需重新验证。共享缓存仅在两台自托管 Runner 运行于同一 Windows 主机时适用，缺失或不匹配时会安全停止或走官方 API 并完整校验。

## 构建后实时行情验证（2026-10-18）

- Runner「APK 构建与签名（手动）」新增 `live_smoke`，默认勾选，手机显示“构建后验证实时行情（失败则停止签名）”。仍须勾选 `execute` 才会执行构建。
- 新任务预约时固定验证选项，并传给私有 Test 的 Windows/云端工作流。源码构建后，单元测试进程显式接收 `STOCK_CHART_LIVE_SMOKE=1` 和 `ALL_MARKET_CHART_LIVE_SMOKE=1`；禁用这次实测的 Gradle 测试缓存与跳过优化。
- 每次先清除旧 XML 与报告。测试后校验 `StockChartLiveTest` 源码中所有测试方法均出现在本次 JUnit XML，且非零执行、无失败、无错误、无跳过。全市场目录测试还要求至少成功检查一个标的周期。
- 私有 Test Run 摘要显示数量；`live-chart-validation` 私有产物保存绑定源码 SHA、Run ID 的 JSON 与原始 JUnit XML（含供应商输出），保留 7 天。失败时也尽量保存诊断证据。
- 仅成功后才生成可签名 handoff，其中附加验证报告。Runner 在进入签名前另外核验报告检查步骤确实成功、报告产物存在；只读元数据，实时数据和详细日志仍留在私有 Test。
- 取消勾选会明确记录 `not_requested`，不能算实时行情已验证。恢复与限次网络恢复始终沿用原任务选项，不受当前表单默认值影响；不会把旧版已签名任务补标为验证成功。
- 旧 SHA 已构建但未实测时，应使用包含此功能的新 Test/main SHA。此改动不自动派发构建或正式发布，仍由原四个手动入口控制。


## 2026-10-31 调整：在线行情验证从 APK 构建链独立

- Runner「APK 构建与签名（手动）」已取消 `live_smoke` 勾选项，新源码构建固定不请求实时行情测试；原 `execute`、`resume`、`source_sha` 和既有构建、Lint、单元测试、签名保护不变。
- Test 仓库 `.github/workflows/market-live-changes.yml` 在 `main` 的行情报价、市场时段、图表、数据源或相应测试源码发生变更时按 `push.paths` 自动运行 `StockChartLiveTest`，使用真实网络和现有证据核验脚本。
- 该独立在线检查不生成 APK、不接触签名材料，也不会作为普通 APK 构建/正式发布的前置门槛；在线检查失败时应单独排查供应商、交易时段和网络环境，不能伪称在线验证已通过。
- 旧任务中持久化的 `liveSmoke=true` 保持原始归属和原始运行证明，续跑不会因为入口取消选项而将其静默改成未验证。
