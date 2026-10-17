# Android 私有生产构建与签名链（2026-10-17）

## 生产入口

[RUNNER 手动受控入口](https://github.com/chaonpu/Runner/actions/workflows/android-production.yml)

- 默认 `execute=false`：读取 Test/main 精确 SHA 和当前登记，不执行私有派发。
- `execute=true`：仅 `chaonpu` 可启动。需 Test 与签名两个独立 Windows Runner 在线且空闲，相关私有工作流无其他活跃运行。
- `source_sha` 留空：仅当既有凭据可读取 Test/main SHA 时自动选择当前提交。目前 Test Actions/Runner/Billing 令牌在该端点仍缺少 `Contents: Read`，只读预检会报告 `manualSourceShaRequired=true`；**不能在 SHA 为空时执行生产构建**。
- `source_sha` 填写完整 40 位 SHA：当源分支 SHA 无法读取时，以人工确认的 SHA 建立唯一预约；在派发后必须校验 GitHub 返回的**原始 Test Run 的 `head_sha` 等于人工指定值**，且 Run 属于 main 的指定工作流。否则立即停止，不进入签名。可从私有 [Test/main](https://github.com/anwpustudent-lab/Test/commits/main) 复制当前最新 SHA（提交可能随时间变化）。
- `resume=true`：必须提供登记的原始 `source_sha`，仅继续核验原始 Test Run 或原始签名 Run，不重新发送已派发的 Test。若已在 POST 后发生未确认的网络错误或尚未登记 Run ID，必须人工核对，不能自动重派。
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
- Test 已成功，签名机离线：保留 Test Run ID，待签名机重新在线后按原 SHA 手动恢复；须在私有 Artifact 有效期内完成。
- 已进入 `signer_reserved` 而未登记签名 Run ID：必须人工查看签名仓库有无原始派发及副作用；调度器不接受自动恢复重派。
- 签名有 Run ID 但状态不明：仅观察原始签名 Run，不能重复签名。
- 签名步骤失败：停止并保留记录，人工检查私有日志后才能决定新源码或显式恢复方案。
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

## 当前限制

- 本次仅新增**Windows 精确 Test → Windows 私有签名**生产执行路径；云端 `Test/android.yml` 与签名仓库旧云端工作流的数据契约不同，尚不具备与这条不可变 handoff 直接互换的生产证明。即使云端额度可用，也不绕过上述校验。云端优先/Windows 自动切换需单独补齐一个接受云端已验证 handoff 的私有签名工作流及回归后再开放。
- 公开工作流能否实际派发取决于 RUNNER Secrets：`TEST_ACTIONS_READ_TOKEN`、`CHAONPU_ACTIONS_READ_TOKEN` 需 Actions 写与运行只读；`TEST_RUNNER_READ_TOKEN`、`RUNNER_READ_TOKEN` 已在实时只读预检中确认可分别读取两个 Windows Runner 的状态。可选 `TEST_SOURCE_READ_TOKEN` 只需精确限定私有 Test 仓库 `Contents: Read`，用于自动读取 `main` SHA；不配置时手动指定 SHA 即可保持源码权限隔离。某个必要权限缺失会保守停止。
- 本次 Windows 私有链已通过真实 Run 和 Artifact 验收；若后续换机器、更新工具或接入云端路径，仍需重新验证。共享缓存仅在两台自托管 Runner 运行于同一 Windows 主机时适用，缺失或不匹配时会安全停止或走官方 API 并完整校验。
