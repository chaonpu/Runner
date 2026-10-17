# 子午汐律 · GitHub Actions 统一调度中心

公开中心负责额度检查、Windows Runner 检测、私有任务单次派发、同一 Job 前台监控和结果登记。策略、持仓、账本、完整私有日志与签名密钥仅留在私有仓库。

## 当前部署

A/US 的受控单任务执行链已接线：私有实时就绪检查 → 临时任务许可 → CAS 预约 → 唯一派发与原 Run 绑定 → 前台监控 → 同一数据库提交回执核验 → 关闭临时许可。**美股 2026-10-16 原生理论补算已真实入账并独立核验。** 原运行计算成功、发布确认失败后，已通过[一次性原提交恢复](https://github.com/chaonpu/Stock/actions/runs/39031059755)完成发布，没有重算策略；原运行仍保留失败结论。

配置默认保持关闭。明确启动的单任务只会临时开启相应市场；任务结束后恢复原配置，历史登记继续防重。没有五分钟 cron，没有后台反复派发，也不会因为某次响应不确定而自动重算。

两个个人账户的免费额度配置均为每月 **2000 分钟**。公开仓库标准托管运行器用量排除在私有额度消耗之外；余额未知时不猜测、不当作耗尽。云端与 Windows 路由仍须通过对应的预算、在线状态和业务门槛。

## 运行策略

打开[受控策略入口](https://github.com/chaonpu/Runner/actions/workflows/production-dispatch.yml)，选择 `stock_a` 或 `stock_us`：

| 输入 | 行为 |
| --- | --- |
| `business_date` 留空 | 按实际时钟、市场时区及已覆盖交易所日历选择最近完成的交易日 |
| `execute=false`（默认） | 只读预检 |
| `execute=true` | 明确运行一次任务，先做私有实时就绪验证 |
| `publication_mode=formal` | 只允许原策略的交易日收盘后正式窗口 |
| `publication_mode=theoretical_catchup` | 只允许原生补算窗口，推进理论持仓与账本；不视为正式报告 |

`force_run` 固定为 false。周末不会伪造交易日或绕过正式报告门槛。同日期已有登记时停止；业务失败或响应未知时先核对原 Run 和实际副作用。

详细执行顺序与异常处理见[单任务运行说明](docs/production-execution.md)，首次真实运行结果见[2026-10-17 验收记录](docs/production-acceptance-20261017.md)。本次为理论补算，尚非正式日报或自动定时运行。

## 验证和边界

公开协调器本地 **243 项回归通过**；私有针对性回归本地 **146 项通过**；真实 Windows 就绪检查 **138 项通过**并完成隔离原生计算、账本回放与行情源探测。公开 CI 结果见安全 CI 入口。这些测试之外，本次还独立核验了真实远端回执、原始文件哈希、父提交、历史净值、交易事实、配置同步及另一市场不变。Windows 数据库检出逐文件比对 Git blob，避免换行转换破坏快照与账本哈希。

私有准备证据必须来自准确源码、日期和模式的首次成功 Run，最终步骤在 300 秒有效期内。普通业务完成要求私有回执核验成功且原业务 Run 整体成功。明确的原提交恢复另需原计算成功、发布失败及恢复 Run 整体成功的证明，并单独登记恢复 Run/SHA；不改写原 Run、源码或协调者绑定。补算记录的 `formalReportVerified` 仍为 false。

当前已接线市场为 A/US。ETF、Android 私有签名和公开正式发布尚未因该入口而启用。APK 暂存与正式发布保持独立；未经相应发布任务授权，不会公开新的安装包或更新用户 APP。

## 其他入口

- [安全 CI](https://github.com/chaonpu/Runner/actions/workflows/validate.yml)
- [按需预检](https://github.com/chaonpu/Runner/actions/workflows/scheduler.yml)
- [无操作防重演练](https://github.com/chaonpu/Runner/actions/workflows/stock-noop-pilot.yml)
- [人工异常核对](https://github.com/chaonpu/Runner/actions/workflows/reconcile.yml)
- [批准后的 APK 公开预发布暂存](https://github.com/chaonpu/Runner/actions/workflows/stage-android-apk.yml)
- [部署说明](docs/deployment.md)
- [此前各阶段验收历史](docs/implementation-history-20261017.md)

公开仓库令牌仅派发明确列入白名单的私有工作流。`ZIWUXILV/database` 由 Stock 内部发布，不由本中心派发；公开正式 APK 发布由其所属仓库的独立发布流程执行。
