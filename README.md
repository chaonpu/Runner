# 子午汐律 · 手动任务中心

RUNNER 仅保留以下 **4 个手动工作流**，均从 Actions → 选择工作流 → **Run workflow** → 分支 `main` 启动。仅仓库所有者 `chaonpu` 可以执行。没有 push、定时、演练或自动重派发入口。

| 工作流 | 作用 | 入口 |
| --- | --- | --- |
| APK 构建与签名（手动） | Test 全量验证、构建 APK、私有隔离签名 | [运行](https://github.com/chaonpu/Runner/actions/workflows/android-production.yml) |
| APK 正式发布（手动） | 验证已签名 APK、准备发布请求或派发正式发布、核验更新清单 | [运行](https://github.com/chaonpu/Runner/actions/workflows/android-publish.yml) |
| A股策略（手动） | 预检、运行 A股策略、推进快照与账本、核验回执 | [运行](https://github.com/chaonpu/Runner/actions/workflows/production-dispatch.yml) |
| 美股策略（手动） | 预检、运行美股策略、推进快照与账本、核验回执 | [运行](https://github.com/chaonpu/Runner/actions/workflows/stock-us.yml) |

所有入口默认 `execute=false`，只读检查；需要真实执行时勾选 `execute`。执行期间留在同一个任务中跟踪，完成后结束；不依赖五分钟定时检查。

## APK 构建与签名

1. 在 **Test/main** 提交要发布的代码，确认 `versionName`、`versionCode` 和 `release/version.json` 一致。正式发布必须比当前公开版本更新；RUNNER 不会替你虚增版本号。
2. 打开「APK 构建与签名」，`source_sha` 填 Test/main 完整 **40 位 SHA**。当前已有令牌可能无法读取分支 SHA，建议明确填写。
3. 首次运行 `resume=false`。可先不勾选 `execute` 检查；真实构建勾选 `execute` 后运行。
4. 成功后 [构建登记](state/android-production.json) 中该 SHA 的 `phase` 应为 `signed_verified`，同时登记 `testRunId`、`signingRunId`。签名包保存在私有签名仓库 `windows-signed-release`，保留 3 天，请及时发布。

额度证据满足保守门槛时 Test 优先云端构建；否则回退 Windows。私有签名始终使用 `ziwuxilv-sign-win`。走 Windows 时 Test 还需 `ziwuxilv-test-win` 在线且空闲。云端任务启动后失败不会自动换机重建。

已有未完成任务只有 `test_started`、`test_verified`、`signer_started` 可使用原 `source_sha` + `resume=true` + `execute=true` 继续。`reserved`、`signer_reserved` 或不确定失败需核对原 Run，不能清空登记后重试。已成功的同一 SHA 不重复构建。

## APK 正式发布

打开「APK 正式发布」，填写：

| 输入 | 填写方法 |
| --- | --- |
| `source_sha` | 构建签名成功的同一个 Test SHA |
| `version_name` | APK 内真实版本号，如 `2.3.5`（仅示例） |
| `version_code` | APK 内真实 Code，如 `118`（仅示例），必须大于当前正式版 |
| `update_log` | 用户可见的更新说明 |
| `mode` | `prepare`、`publish` 或 `verify`，见下方 |
| `execute` | 不勾选只检查；勾选后才真实操作 |

**无需增加令牌的完整手动发布：**

1. 选 `prepare`，勾选 `execute`。此步会下载私有签名包，只公开严格白名单的已签名 APK 传输 ZIP 及校验凭据；不会包含源码或密钥，也不会切换 APP 正式更新清单。
2. 在成功运行的 **Artifacts** 下载 `apk-publication-request`，解压取得 `android-publish-request.json`。
3. 打开 [ziwuxilv 正式发布入口](https://github.com/chaonpu/ziwuxilv/actions/workflows/publish-android-release.yml)，点 **Run workflow**，分支 `main`，将 JSON **完整内容**粘贴进 `publish_request`，再运行。
4. 等该发布任务成功后，回 RUNNER 用相同输入、`mode=verify`、`execute=true` 核验。结果必须为 `published=true`，才表示清单和公开下载的 APK 哈希已核验成功。

**可选的一键发布：** 在 RUNNER 配置 `ZIWUXILV_ACTIONS_WRITE_TOKEN`，仅授权公开仓库 `chaonpu/ziwuxilv` 的 **Actions: Read and write**。然后选择 `mode=publish`、勾选 `execute`。此方式派发发布仓库自身工作流，由该仓库自己的 `GITHUB_TOKEN` 写 Release 和更新清单。原有私有仓库令牌不扩权。缺少专用令牌时会在公开签名包之前停止，并提示使用 `prepare`。

发布会校验真实包名、版本、原生产证书、SHA-256、准确 Test/签名 Run，以及正式下载地址。`prepare` 成功只代表**请求已准备**。同版本重复执行不会重复暂存或重复派发；发布中断后保留准确 Run ID。状态为 `staging_reserved`、`dispatch_reserved` 时先人工核对原结果，勿反复点击。正常继续已有 `publishing` 任务请新建一次手动运行，不使用旧 Run 的 Re-run。

> 当前公开版本仍为 **2.3.4 (117)**。此前新链签名验证也使用了相同版本，不能再次当作新正式版发布；请在 Test 提交真实新版本后按上述步骤操作。

## A股与美股策略

分别打开对应工作流，输入只有三项：

| 输入 | 用法 |
| --- | --- |
| `business_date` | **平时留空**，按实际时间、市场时区和交易所日历选择最近已收盘交易日；补算指定日期时才填写 `YYYY-MM-DD` |
| `publication_mode` | `formal`：该市场当地交易日收盘后的正式运行；`theoretical_catchup`：历史交易日理论补算 |
| `execute` | 不勾选预检；勾选运行一次 |

正常收盘后运行选择 `formal`；周末、跨过市场当地日期或补算历史选择 `theoretical_catchup`。补算推进理论持仓、快照与账本，不冒充当日正式报告、不发送正式策略邮件。美股日期按纽约当地交易日，不按北京时间换日。

运行前需 Windows `ziwuxilv-stock-win` 在线且空闲，以完成私有实时就绪检查；这项检查目前即使云端额度充足也需要 Windows。业务计算再按已核实账户额度选云端或 Windows。两市场共用串行锁，避免同时改账本。运行结束关闭本次临时许可，日常配置保持 OFF；这是手动执行模式，不表示功能禁用。

同一市场、同一日期已有登记会停止，不能重复调仓/入账。遇到 `hold` 或失败，先核对日志中的原 Run ID 和 [任务登记](state/tasks.json)，不要删除记录或盲目 Re-run。成功以**下游业务成功 + 数据库回执核验成功**为准。

## 维护与验证

本次精简删除了 10 个旧工作流文件（旧预检、canary、演练、push 启动、一次性恢复、独立暂存及自动 CI），增加美股和统一发布两个入口，最终净保留 4 个。历史状态、防重记录和底层测试保留，未删除实际 APK、历史运行或账本。

维护者在本地运行：`python3 -m unittest discover -s tests -p 'test_*.py' -v`。当前交易所日历覆盖 2026 年，跨年需先更新 `config/exchange_schedule_2026.json`，覆盖不足时系统停止执行。

旧的部署、演练及阶段性说明保留作历史记录；**当前使用以本 README 为准**。

本次验证证据及实际运行边界见 [2026-10-18 手动入口验收](docs/manual-workflows-acceptance-20261018.md)。
