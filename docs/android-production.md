# Android 私有生产构建与签名链（2026-10-17）

## 生产入口

[RUNNER 手动受控入口](https://github.com/chaonpu/Runner/actions/workflows/android-production.yml)

- 默认 `execute=false`：读取 Test/main 精确 SHA 和当前登记，不执行私有派发。
- `execute=true`：仅 `chaonpu` 可启动。需 Test 与签名两个独立 Windows Runner 在线且空闲，相关私有工作流无其他活跃运行。
- `source_sha` 留空：首次执行以 Test/main 当前提交为来源；若填写，必须等于首次执行时的 Test/main。
- `resume=true`：必须提供登记的原始 `source_sha`，仅继续核验原始 Test Run 或原始签名 Run，不重新发送已派发的 Test。若已在 POST 后发生未确认的网络错误或尚未登记 Run ID，必须人工核对，不能自动重派。
- 同一源码 SHA 的构建身份只保留一项公开状态登记；已签名成功的 SHA 不自动重复构建。

## 安全派发顺序

1. 从私有 Test 的 `main` 元数据获取当前 SHA，只保存提交哈希。
2. 检查两个独立 Windows 服务及私有工作流活跃状态；在 `state/android-production.json` 以 GitHub Contents SHA CAS 预约唯一 SHA。
3. 派发 `anwpustudent-lab/Test/.github/workflows/test-self-hosted.yml`，要求 GitHub API 返回实际 Run ID。
4. 同一 RUNNER Job 前台持续检查该 Run 的仓库、触发方式、分支、SHA、重试次数、最终成功状态和执行 Runner；验证存在 `windows-test-verified` 私有 handoff artifact。
5. 原 Test Run 成功且签名 Runner 在线后，派发 `chaonpu/ziwuxilv-android/.github/workflows/windows-signing-verified-handoff.yml`；仅传入原 Test Run ID 与源码 SHA。
6. 签名仓库在私有 Runner 上校验完整源码/unsigned APK SHA256、测试与 lint、固定生产证书指纹、包名与版本，完成签名；密钥始终留在私有工作区。
7. RUNNER 核验原始签名 Run 成功、实际 Runner 匹配，并确认存在非空 `windows-signed-release` 私有 artifact 后，登记 `signed_verified`。

公开 RUNNER 不读取私有源码、私有产物、签名密钥、完整构建日志，也不运行 Test 的业务源码。

## 停止与恢复

- 源码已预约，但 GitHub POST 返回不确定：原预约保留；不自动重试，先从私有仓库核实是否已经开始运行。
- Test 有精确 Run ID 但执行中断：手动 `resume=true` + 原 SHA，继续监控同一 Run，禁止新建 Test Run。
- Test 已成功，签名机离线：保留 Test Run ID，待签名机重新在线后按原 SHA 手动恢复；须在私有 Artifact 有效期内完成。
- 签名有 Run ID 但状态不明：仅观察原始签名 Run，不能重复签名。
- 签名步骤失败：停止并保留记录，人工检查私有日志后才能决定新源码或显式恢复方案。
- 本入口无定时五分钟轮询；一旦启动，同一公开 Job 前台监控至完成或保守停止。

## 公开 APK 发布边界

本入口完成后只获得**私有签名成功**，并不等于正式 APK 已发布。若要公开暂存，另行使用 [签名 APK 独立审批暂存](https://github.com/chaonpu/Runner/actions/workflows/stage-android-apk.yml)，按其要求提供来源 SHA、Test Run ID、签名 Run ID、版本及人工审批短语。正式发布继续使用 `chaonpu/ziwuxilv` 自有发布保护流程，必须经过现有版本递增与更新清单校验。未经审批，绝不自动发布。

## 当前限制

- 本次仅新增**Windows 精确 Test → Windows 私有签名**生产执行路径；云端 `Test/android.yml` 与签名仓库旧云端工作流的数据契约不同，尚不具备与这条不可变 handoff 直接互换的生产证明。即使云端额度可用，也不绕过上述校验。云端优先/Windows 自动切换需单独补齐一个接受云端已验证 handoff 的私有签名工作流及回归后再开放。
- 公开工作流能否实际派发取决于 RUNNER Secrets：`TEST_ACTIONS_READ_TOKEN`、`CHAONPU_ACTIONS_READ_TOKEN` 需 Actions 写与运行只读；`TEST_BILLING_READ_TOKEN` 需 Test Runner 状态读取权限。某个权限缺失会保守停止。
- 实际构建是否成功必须由**本入口真实运行**、两个原始私有 Run 及签名 Artifact 证明；公开 CI 回归通过不能替代跨仓库端到端验收。
