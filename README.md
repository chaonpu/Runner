# 子午汐律 · 任务调度中心

RUNNER 保留以下 **4 个手动业务工作流**，另有 **1 个每日历史清理工作流**。手动入口从 Actions → 选择工作流 → **Run workflow** → 分支 `main` 启动，仅仓库所有者 `chaonpu` 可以执行。策略、构建和发布没有 push 或定时派发入口。

| 工作流 | 作用 | 入口 |
| --- | --- | --- |
| APK 构建与签名（手动） | Test 全量验证、构建 APK、私有隔离签名 | [运行](https://github.com/chaonpu/Runner/actions/workflows/android-production.yml) |
| APK 正式发布（手动） | 验证已签名 APK、准备发布请求或派发正式发布、核验更新清单 | [运行](https://github.com/chaonpu/Runner/actions/workflows/android-publish.yml) |
| A股策略（手动） | 预检、运行 A股策略、推进快照与账本、核验回执 | [运行](https://github.com/chaonpu/Runner/actions/workflows/production-dispatch.yml) |
| 美股策略（手动） | 预检、运行美股策略、推进快照与账本、核验回执 | [运行](https://github.com/chaonpu/Runner/actions/workflows/stock-us.yml) |

四个业务入口默认 `execute=false`，只读检查；需要真实执行时勾选 `execute`。执行期间留在同一个任务中跟踪，完成后结束；不依赖五分钟定时检查。

## APK 构建与签名

1. 在 **Test/main** 提交要发布的代码，确认 `versionName`、`versionCode` 和 `release/version.json` 一致。正式发布必须比当前公开版本更新；RUNNER 不会替你虚增版本号。
2. 打开「APK 构建与签名」，`source_sha` 填 Test/main 完整 **40 位 SHA**。当前已有令牌可能无法读取分支 SHA，建议明确填写。
3. 首次运行 `resume=false`。可先不勾选 `execute` 检查；真实构建勾选 `execute` 后运行。
4. 成功后 [构建登记](state/android-production.json) 中该 SHA 的 `phase` 应为 `signed_verified`，同时登记 `testRunId`、`signingRunId`。签名包保存在私有签名仓库 `windows-signed-release`，保留 3 天，请及时发布。

剩余额度 **≥100 分钟使用云端，<100 分钟才转自托管**，APK 构建、A股和美股策略采用同一门槛，不再叠加预计耗时或预留分钟数。公开标准运行器用量不扣减私有仓库免费额度；额度无法读取、证据过期或口径无法核实时暂停派发，不当作低余额。Test 构建与私有签名按各自账户额度独立选路；签名额度 ≥100 分钟使用云端，否则要求 `ziwuxilv-sign-win` 在线且空闲。走 Windows 时 Test 还需 `ziwuxilv-test-win` 在线且空闲。云端任务启动后失败不会自动换机重建。

已有未完成任务只有 `test_started`、`test_verified`、`signer_started` 可使用原 `source_sha` + `resume=true` + `execute=true` 继续。`reserved`、`signer_reserved` 或不确定失败需核对原 Run，不能清空登记后重试。已成功的同一 SHA 不重复构建。

## APK 正式发布

打开「APK 正式发布」，只需两项：

| 输入 | 用法 |
| --- | --- |
| `update_log` | 可选的用户可见更新说明；留空读取所选源码对应版本的具体更新说明 |
| `execute` | 不勾选仅检查；勾选后正式发布并核验下载 |

1. 先完成「APK 构建与签名」。
2. 打开发布工作流，分支 `main`，按需填写更新说明，勾选 `execute` 后运行。
3. 等待摘要显示正式发布成功；不需要输入 SHA、版本号、Code 或选择模式。

自动使用最新登记的构建，必须已达到 `signed_verified`；最新构建尚未完成时停止，不退回旧包。源码 SHA 和准确 Test／签名 Run 从登记读取，版本号和 Code 从该签名产物读取，并核验 APK 实际包名、版本、原生产证书和 SHA-256。选中的版本、源码与 Run 会显示在摘要中。只读取私有签名产物及运行元数据，不读取 Test 源码。

有唯一的已准备或发布中的任务时，自动继续原任务；更新说明留空即可沿用原文。`staging_reserved`、`dispatch_reserved` 表示结果不确定，仍需核对原 Run，不能盲目重试。相同版本不会重复暂存或派发；已完成的重复运行只核验已有发布。未完成任务超过一个时停止供人工核对。

复用现有 `CHAONPU_ACTIONS_READ_TOKEN`，需包含公开仓库 `chaonpu/ziwuxilv` 的 **Actions: Read and write**；无需新令牌。公开 Release 和更新清单由发布仓库自身的 `GITHUB_TOKEN` 写入。发布后核验正式清单、准确版本、证书、更新说明、下载地址及 APK 哈希，只有 `published=true` 才代表成功。请新建手动运行，不用旧 Run 的 Re-run。

底层保留 `prepare`／`verify` 接口供维护核对，日常发布界面不再展示这些模式。

## A股与美股策略

分别打开对应工作流，只需一个选项：

| 输入 | 用法 |
| --- | --- |
| `execute` | 不勾选仅预检；勾选后自动选日期和模式并运行一次 |

无需输入日期或选择补算模式。手动启动后，按市场时区和交易所日历选最近已收盘交易日：该市场当地仍是这个交易日时正式运行；已跨日、周末或节假日时自动使用 `theoretical_catchup`。美股使用纽约交易日，支持夏令时和提前收盘。自动选择的日期、模式会写入运行摘要，预检也显示相同计划。

已有已核验任务或权威数据已到目标日时跳过。需要推进时，原有账本引擎按历史行情补齐两次快照间的每日收益；补算推进理论持仓、快照与账本，不发送正式策略邮件。当前原生策略仅计算最近已收盘交易日，不逐日重放漏跑期间的历史选股和调仓决策；缺行情或发现未入账调仓时停止，不编造历史结果。跨出已配置交易所日历范围时停止，需先更新日历。

策略业务仍只允许手动触发，每日维护不会派发策略任务。内部兼容的日期参数也必须匹配最近已收盘交易日。

预检与业务计算都按账户额度选择：≥100 分钟使用云端，<100 分钟要求 Windows `ziwuxilv-stock-win` 在线且空闲。云端预检不依赖 Windows 在线。两市场共用串行锁，避免同时改账本。运行结束关闭本次临时许可，日常配置保持 OFF；这是手动执行模式，不表示功能禁用。

同一市场、同一日期已有 **verified** 登记，或私有检查确认权威数据已经覆盖该日期时，工作流会绿色完成并显示“已有结果，已跳过”；不会重复调仓、入账或生成新的正式报告。未完成、失败待核对或响应不确定的登记仍会停止，不会冒充已完成。遇到 `hold` 或失败，先核对日志中的原 Run ID 和 [任务登记](state/tasks.json)，不要删除记录或盲目 Re-run。成功以**下游业务成功 + 数据库回执核验成功**为准。

## 维护与验证

每日维护入口：[私有仓库历史清理（每日）](https://github.com/chaonpu/Runner/actions/workflows/private-history-cleanup.yml)。计划为北京时间每天 **04:30**（UTC cron：`30 20 * * *`），在公开 Runner 的 GitHub 云端执行，电脑关机不影响。

范围固定为 `chaonpu/Stock`、`anwpustudent-lab/Test`、`chaonpu/ziwuxilv-android`。先用现有令牌读取每仓 main 的准确工作流文件列表，只删除文件已移除的已完成运行。现用 10 个下游另有固定保护，新增工作流也受当前文件列表保护；排队、运行中的任务不删除。每次删除前复核运行身份与 main 未变化；读取失败时不授权删除。不会修改源码、账本、Release 或现用工作流的历史与产物。删除旧运行时，其关联日志及 Actions artifacts 随运行删除。

手动运行默认 `dry_run=true` 仅预览，取消勾选才删除；定时运行自动执行。每仓每次最多删除 500 条，余量留到下一次。摘要显示各仓扫描、待删、已删及延后数量，计数报告保留 7 天；某仓失败仍检查其余仓库，并将本次维护标记为失败。database 与公开发布仓不在此维护范围。

维护者在本地运行：`python3 -m unittest discover -s tests -p 'test_*.py' -v`。当前交易所日历覆盖 2026 年，跨年需先更新 `config/exchange_schedule_2026.json`，覆盖不足时系统停止执行。

旧的部署、演练及阶段性说明保留作历史记录；**当前使用以本 README 为准**。

本次验证证据及实际运行边界见 [2026-10-18 手动入口验收](docs/manual-workflows-acceptance-20261018.md)。
