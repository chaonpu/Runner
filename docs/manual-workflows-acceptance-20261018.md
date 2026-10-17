# 四个手动业务入口验收

时间：北京时间 2026-10-18 00:10 左右。

- RUNNER 最终仅保留 `android-production.yml`、`android-publish.yml`、`production-dispatch.yml`、`stock-us.yml`，事件均为 `workflow_dispatch`。没有 push、schedule、PR、canary 或临时恢复工作流。
- 迁移线上验证：[Run 39048195857](https://github.com/chaonpu/Runner/actions/runs/39048195857)，对应提交 `4ab54025c3c684af59446f1bc159847eba8c0c11`，279 项测试全部通过，任务整体成功。临时 CI 已在验证后删除。
- 最终移除 CI 后，本地完整 279 项仍通过；新增发布 HTTP 请求显式设置 JSON Content-Type。
- APK 实时只读预检：Test Runner、签名 Runner 在线且空闲；Windows 路由，原因 `remaining_estimate_below_conservative_threshold`；两个 Test 构建入口与私有签名入口均没有活动任务。现有凭据不能验证 Test/main 分支 SHA，手动构建需输入准确 `source_sha`。
- Stock 实时检查：Windows Runner 在线、标签核实，两市场均 `no_active_run`。A股缺少新鲜私有准备证据，美股原证据已过期，这是只读诊断结果；真实执行入口先派发私有就绪检查生成本次证据，不复用旧证明。未在本次验收中实际运行或重算策略。
- [公开发布后端更新](https://github.com/chaonpu/ziwuxilv/commit/42c2aa51a81737ec268b16f2392b64a0026fd9f5)：接收 `publish_request` 手动 JSON；在任何后续 git 切换前固定到临时文件，避免读到旧请求；校验失败不写发布状态。
- 公开发布既有 guard 13 项全部通过；手动 JSON 传输执行检查通过：合法 JSON 往返一致、真实换行、无效对象被拒绝。所有工作流 YAML 解析及其中 Python 脚本语法检查通过。
- 发布链测试覆盖：未签名源、版本冲突、云端 Test 来源、不可信调用者、跨市场入口、预览无写入、无令牌提前停止、准备不冒充正式发布、唯一 Run 绑定、响应不确定禁止重派发、继续原发布任务、手动发布后核验。
- 已具备无需新令牌的发布方式：RUNNER `prepare` → 发布仓库手动粘贴 JSON → RUNNER `verify`。可选的 `publish` 自动派发需要另配仅覆盖公开仓库的 `ZIWUXILV_ACTIONS_WRITE_TOKEN`；本次未创建或扩权令牌。
- 正式 APK 仍为 2.3.4 (117)，本次未改变版本、Release、更新清单、账本或持仓，未发送策略邮件。只有在 Test 中完成真实新版本构建签名后，才能进行下一次正式发布；本次没有把代码回归或只读检查称为新正式版端到端发布验收。

使用方法以 [README](../README.md) 为准。过去部署、演练、恢复文档仅作历史资料，不代表当前仍保留那些工作流入口。
