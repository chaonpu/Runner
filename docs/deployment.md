# 统一调度中心部署与迁移计划（2026-10-17）

## 2026-10-17 生产主分支之外的两次真实远端 Git 验收与协调准入门禁

- [Stock #38985616567](https://github.com/chaonpu/Stock/actions/runs/38985616567)：在实际 Windows 自托管 Runner 上使用当前私有 database 凭据，仅向**`ZIWUXILV/database` 审计分支 `audit/windows-git-ack-20261017`** 写入一份固定合成 `ci_audits` 标记。**一次 `git push` 后通过独立 `git ls-remote` 核对准确远端 SHA**，Job 成功，事后用 GitHub API 复查审计分支文件存在。正式 database main **未变化**，没有真实交易数据/APP 快照/账本写入。审计分支不会合并进业务 main。
- 另在公开 `chaonpu/Runner` 的 **`audit/runner-cas-20261017`** 分支建立只含合成状态的 `state/audits/runner-cas-20261017.json`，通过 GitHub Contents API 的前置 blob SHA 完成一次真实条件更新（CAS），独立 GET 回读内容确认正确。再故意以旧 blob SHA 尝试覆盖，GitHub 返回 **409 Conflict**，原状态不变。正式 RUNNER main 的 `state/tasks.json` 仍为 0 条，不创建生产登记，不包含任何私有数据。
- **已存在的真实跨库无业务派发结果**：RUNNER public `state/pilot_runs.json` 记录 `pilot:stock:2026-10-17`（Runner Run `38961235915` → Stock Run `38961249562`）与 `pilot:stock:2026-10-18`（Runner Run `38967023305` → Stock Run `38967039826`），两项均已登记 `completed_success`。它们运行的仅是 `runner-dispatch-canary.yml`，**不是** A股/美股正式策略。
- Stock 新增 `private_runner_admission.py`，且两套 A/US 工作流都在协调任务任何业务之前，独立向公开 Runner 读取 `config/repositories.json`、`state/tasks.json`、原始 Runner Run 元数据。只有当生产总开关及该任务开关均打开、该任务唯一登记、公开登记已把**准确 Stock Run ID** 绑定到准确协调 Run ID、执行路线及允许的工作流一致、来源上下文可信时才能通过。原 Stock Run 绑定可在单次工作流内部有上限地等待（最多五轮，间隔 10 秒），但不额外派发、重算、重复写入或后台守护。未登记/冲突/外部网络不可用立即 HOLD。
- [Stock Windows #38986254988](https://github.com/chaonpu/Stock/actions/runs/38986254988) **Success**：28/28 项 private ACK、Windows mode、协调入场来源模拟测试全通过，并通过私有 GitHub API 对当前 A/US workflow 的准确源码位置做只读契约检验；没有触发生产策略、私有 database 正式写入或邮件。新增临时自动触发已删除。
- **仍未满足的正式上线条件**：生产工作流 `production-dispatch.yml` 当前仍故意采用**只读安全门**，没有权限或代码真实调度 A/US；GitHub 托管免费余额不能仅以已消耗分钟推断；Windows 网络可靠性存在历史 443 超时；当前 registry 没有生产任务，未能用真实正式运行证明 Runner ID → Stock Run ID → database main 同提交回执 → 独立确认的闭环。必须逐项证实后再开启生产，不得把 audit 分支或 canary 成功写成 `businessVerified`。

## 2026-10-17 Git 远端写入 ACK 不确定性处理与 Windows 回执来源匹配（生产仍锁闭）

- 私有 Stock 新增 `private_push_confirmation.py` 和 8 项合成测试；**仅带 coordinator task_id 的 A/US 正式工作流**使用。必须先生成正式行情结果、安全通过本次运行日期和覆盖校验、计算并提交同一次账本与私有回执，然后核对身份、任务日期、数据库唯一允许的 origin、同一 Git commit 的市场效应与私有 receipt。随后仅允许 **一次** `git push origin HEAD:main`，并单独通过**只读** `git ls-remote` 验证远端 main SHA 与本次拟提交 SHA 完全相同。如果客户端 push 响应丢失但 GitHub 已写入同一 commit，可以确认“提交存在”，但不会再次推送；如果远端无法读取或不等，则返回 HOLD，绝不执行第二次业务或第二次 Git push，也不授予 `verified`。
- [Stock Windows #38984600065](https://github.com/chaonpu/Stock/actions/runs/38984600065) **Success**：完整隔离环境与本地临时裸 Git 8 种模拟，**Python 117/117 OK、Node 18/18 PASS**。该环境曾在一轮出现检出异常，因此另外独立以准确私有 GitHub API 获取文件，不依赖 Git 检出；[#38984800849](https://github.com/chaonpu/Stock/actions/runs/38984800849) **Success，8 项合成单测通过**，并校验 A/US 都仅在协调任务中使用新 push 回查器。
- `private_coordinator_link.py` 原仅认 `cloud/ubuntu`，现在可在**公开 registry 路线为 windows 且配置明确设置相同 Stock 工作流的 `windowsWorkflow`**时，核对 `receiptRunnerMode=windows` 和实际 workflow_dispatch 的 `runner_mode`（如存在）。无 allowlist 或模式不一致即拒绝，永远只输出 `identities_linked_read_only`，不会确认业务完结或授权自动补偿。**目前公开 config 的 A/US `windowsWorkflow=null`，故 Windows 回执仍未被允许用于生产；总派发开关和所有任务开关均 false。**
- [Stock #38985112517](https://github.com/chaonpu/Stock/actions/runs/38985112517) **Success**：使用只读私有 GitHub API 校验真实 A/US 发布步骤，并执行 **19 项合成回归**，包括 Windows 路线显式 allowlist、运行器模式、回执来源一致性及 Git 推送丢 ACK 回读。无真实 database 写入、无策略执行、无重试、无公开私有文件。
- **当前界限**：能安全确认一个已写入的 Git commit，不等于核对了生产真实业务的交易日收益和来源行情，更不等于 RUNNER 可以在远端响应不确定时自动再派任务。剩余阻断包括私有完整 Git 检出与未来推送在 Windows 上的网络稳定性、真实完整源交易数据、RUNNER 任务登记与实际 Stock Run 绑定和独立回执认证。生产工作流仍只读，不能仅凭模拟测试改成自动写账。

## 2026-10-17 Windows Python/Node 账本合成实测通过（仍非真实 GitHub 入账）

- 最新 [Stock Windows Run #38983987118](https://github.com/chaonpu/Stock/actions/runs/38983987118) 完整 **Success**。在私有 Windows Runner 第一次成功检出 Stock 源码，使用 Windows 隔离 venv 安装实际 `requirements.txt` 和 `tzdata`，核实 A股/纽约时区，运行现有真实账本/回执代码的**合成数据**测试，日志 `Ran 109 tests in 10.193s / OK`；Node 22 四组收益贡献和历史回补测试 **18 pass / 0 fail**；退出后成功删除临时源码和 venv。
- [Stock #38983133101](https://github.com/chaonpu/Stock/actions/runs/38983133101) 的 Windows **本地** Git 裸仓库模拟仍为 Success：A_SHARE/US 两市场均先成功提交/推送同一次合成账本与回执，再拒绝竞争分支非快进推送，并发现重复回执。仅对临时目录的本地 Git remote 写入，未对真实数据库或 Stock 发起 push。
- 当前两套正式 `run-a-share.yml`、`run-us.yml` 已明确**带 `task_id` 的协调任务只运行一次业务策略**，原有无标记任务最多 3 次策略尝试保持不变。Windows 源码与 database 检出前置最多 3 次恢复，与业务任务单次执行严格分离；真实的业务运行和远程 Git 推送未被触发。
- 之前 [Stock #38983291254](https://github.com/chaonpu/Stock/actions/runs/38983291254) 的模拟单测在系统 Python 缺少 `tzdata` 时提前停止；[#38983694988](https://github.com/chaonpu/Stock/actions/runs/38983694988) 安装依赖成功，但 PowerShell 5.1 引号转义破坏 `python -c` 时区代码，未进入断言。已使用 stdin/here-string 解决，并通过 #38983987118 确认。错误不会被误记为账本算法失败。
- 本轮临时验收 push 路径全部删除，`windows-bounded-checkout-recovery.yml` 和 `windows-local-git-transaction-audit.yml` 仅允许人工 `workflow_dispatch`，没有新增定时巡检、真实策略、邮件、APK、database 变动。
- **尚未闭环**：GitHub 私有仓库完整检出可靠性仍存在时好时坏、向真实 `ZIWUXILV/database` 的有证据安全写入及不确定结果后的只读确认未验收、上游真实收盘数据与云端/Windows 结果一致性未证明、Runner 账户额度/派发来源/生产 `verified` 链路仍未完整通过。所有生产自动派发开关保持关闭；不得把安全合成测试通过当作可直接启用自动补算。

## 2026-10-17 业务任务单次执行与 Windows 本地 Git 并发事务验证

- 私有 Stock 已分别修改 `run-a-share.yml` / `run-us.yml`：只在收到协调任务 `task_id` 时，策略运行次数由旧 3 次降为**仅 1 次**，避免业务已经产生部分副作用后还重试。原有**无协调标记**的 A/US 工作流仍为 3 次旧机制。Windows 仅在业务前对 Stock/DB 两个仓库进行最多 3 次检出，二者不可混同；任何失败不能自动再次派发业务或自动推进旧账本。
- [Stock Windows 本地 Git 事务验收 #38983133101](https://github.com/chaonpu/Stock/actions/runs/38983133101) 已 **Success**，在该机器的完全临时本地 Git 裸 remote 对 A_SHARE、US 分别模拟：合成账本与回执同提交，第一次本地推送成功，第二个并发客户端的 non-fast-forward 推送被拒，重复回执可被识别。公开/私有 GitHub 远端未写入，不访问真实 database，不执行策略或发邮件。额外通过当前 GitHub API 精确提交核对两套正式 A/US workflow 的单次业务执行/旧式重试逻辑，记录为 `coordinated_at_most_once_strategy_contract_passed`。
- 扩展 Stock 原有的 Windows 安全检出测试，以便在实际 private checkout 成功之后运行 Python `database_ledger_sync/publish_latest` 模拟单测及四套 Node 贡献/回补计算单测。验收工作流不启动策略、不获取业务密钥、不检出 database。新一轮 [Stock #38983291254](https://github.com/chaonpu/Stock/actions/runs/38983291254) 必须以最终日志为准；源码检出失败时只保留网络阻断结论，不假称账本程序通过。
- 本地 Git 的非快进拒绝只能证明标准 Git 提交和并发机制，**不能证明真实 GitHub database push 成功**，也不能解决“远端已经提交但本地因断线没有收到成功响应”的不确定性。正式自动补偿仍需只读远端回执识别、原始 RUNNER/Stock Run 来源、业务日期及正确事务的独立验证。继续不启用 `dispatchEnabled`、任何 `enabledForDispatch` 和生产任务写入。

## 2026-10-17 已实现 Windows 两私有仓库的有限检出恢复，生产派发继续关闭

- 根因状态：`ziwuxilv-stock-win` 到 `github.com:443` 的 Git HTTPS 连接**偶发超时/重置**，虽可通过 GitHub API、短请求及某些完整检出，仍不应当成网络已根治。Stock 完整 `checkout@v7` 两批成功率为 **3/3、0/3**，说明可靠性不足。
- Stock [安全检出恢复验收 #38981835435](https://github.com/chaonpu/Stock/actions/runs/38981835435) 已在 Windows 真实故障下通过：第一轮失败，等待 25 秒，第二轮成功并完成 3 个 Python 文件的**仅语法解析**，第三轮自动跳过；没有策略执行、无业务数据访问。独立诊断只读，临时文件清理成功。
- `chaonpu/Stock/main` 的 **`run-a-share.yml`、`run-us.yml` 已接入恢复方案**，仅在 Windows 协调执行路径下，在任何策略启动前为 Stock 源码与 `ZIWUXILV/database` 两个仓库各提供最多三轮 `actions/checkout@v7`，前两次失败后分别等待 25 秒、45 秒；每次严格使用 GitHub `outcome`，一旦成功不再重试；全部失败立即阻断。还会校验 Stock HEAD 与触发 Run 的 `GITHUB_SHA` 一致，且 database 工作目录和 origin 正确。原 Ubuntu 云端单次检出、策略规则、邮件、仓库存储和单个业务任务的去重机制保持不变。
- Stock [数据库真实只读检出验收 #38982711532](https://github.com/chaonpu/Stock/actions/runs/38982711532) 完成，第一轮即成功，后两轮跳过；校验了正确私有 Git 远端且已删除本地临时检出目录。使用原有 database Token 做**不保留凭据的只读 checkout**，没有输出持仓/账本或推进 database。
- Stock [Windows API 无检出契约验收 #38982579267](https://github.com/chaonpu/Stock/actions/runs/38982579267) 成功：使用 GitHub 本仓库只读 API，对准确提交的 A/US 两条工作流进行静态契约核验，并完成 3 个 Python、2 个 Node 的语法检查；确认源码与 database 保护都在业务发生之前，检出恢复不会触发真实策略重试。
- **重要**：这是 **代码层有限缓解措施**，不是系统级修复 `github.com` 网络。未来 Windows 检出仍可能全部失败并安全退出；即使检出成功，也不能自动证明行情源稳定、后续 Git push、实际策略结果和私有回执已经通过端到端验收。不能据此打开 RUNNER 自动派发，不能把超时重试扩展到调仓或数据库写操作。
- 所有额外验收入口当前仅手动 `workflow_dispatch`，一次性 `push` 触发均已撤销，没有五分钟定时复核、自动业务重算、自动邮件或 APK 构建。生产总开关 `dispatchEnabled=false`、四项 `enabledForDispatch=false`，等待后续单独受控实测和闭环确认。

## 2026-10-17 Windows 正式 `checkout@v7` 验收发现生产级网络可靠性阻断

- 私有 Stock 新增仅手动 `windows-checkout-acceptance.yml`：在 `ziwuxilv-stock-win` 上连续三次使用**与正式 A股/美股策略一致的 `actions/checkout@v7`**，每次使用独立本机工作目录与只读 Job Token，不访问 database。成功后才允许尝试静态 `compile`、`node --check` 和私有合成测试；任何 checkout 真实 `outcome` 为失败，最终验收明确失败，绝不以 `continue-on-error` 的绿色 `conclusion` 作为成功。
- [第一批 #38980440449](https://github.com/chaonpu/Stock/actions/runs/38980440449)：**3/3 真正检出成功**，随后 PowerShell 5.1 直接书写中文内联路径变成问号，导致测试脚本路径失败，不构成策略语法异常。用 ASCII Unicode 转义修正该测试脚本。
- [第二批 #38980645250](https://github.com/chaonpu/Stock/actions/runs/38980645250)：真实 `outcome` 为 **0/3 成功**，全部失败于 `github.com:443` 超时/连接重置。该批静态与合成检查均未执行，强制汇总步骤正确拒绝通过。两批合计 **3/6**，实际完整检出存在明显间歇性故障；即使同一分钟此前 Git 引用或浅层抓取成功，也不能忽略此证据。严禁在此基础上自动切至 Windows 生产策略或自动重试业务任务。
- [私有源码 API 无检出语法验收 #38981450738](https://github.com/chaonpu/Stock/actions/runs/38981450738)：新增仅人工 `windows-private-syntax-api.yml`，使用 Stock 自己的 `contents:read` Token 通过 `api.github.com` 获取当前 GitHub Run 确切提交的两份中文策略及辅助 Python、两份 Node 账本脚本；**3 个 Python 文件和 2 个 Node 文件纯语法检查通过**，输出不含源码或提交 SHA。此方式成功避开 Git HTTPS，但**只是验证语法，不是无 Git 源码入口的真实策略执行路径**，不替换生产版 `actions/checkout`。
- 已修正手动 checkout 验收脚本的展示，明确真实 `outcome`，尝试清除临时工作目录，避免误认绿色为成功。所有本轮诊断 workflow 的临时 `push` 入口已移除，没有新增 schedule、后台轮询或正式派发。
- 后续应先做 Windows 出站到 `github.com:443` 的网络稳定性排查（时间分布、DNS/代理/安全软件/运营商路由、系统代理与 Git 代理差异），**优先修好网络而非擅自将生产源码转移到公开 RUNNER 或 Gitee**。如果需要进程内的安全下载重试，必须仅允许**策略开始前**的检出阶段，且带严格上限，不得重派实际业务工作流或复用旧账本并声称本次成功。
- `dispatchEnabled`、各任务 `enabledForDispatch` 必须保持 `false`，公共 `state/tasks.json` 不得新增未经完整来源注册的生产状态。生产 `verified` 仍需独立私有数据库回执和 RUNNER 原始 Run 证据。

## 2026-10-17 Windows GitHub 连接诊断：公开/私有 Git 引用和对象传输均通过

- 之前的 `actions/checkout` 偶尔出现 `github.com:443` 连接超时；经过 Node 22、Git Bash、Python3.14 等 Windows 工具链与依赖验收后，进一步将普通 TLS/端口检查和真正 Git 协议测试分开，防止把 TLS 握手成功当作私有 Git 检出成功。
- [公开 Git 只读诊断 #38979632392](https://github.com/chaonpu/Stock/actions/runs/38979632392)：Windows 上针对**公开** `chaonpu/Runner.git` 做 3 次默认 `git ls-remote` 和 3 次强制 HTTP/1.1 `git ls-remote`，**6/6 成功**（约 1.05–1.7 秒）。DNS 本次解析 IPv4 记录、未解析 IPv6，主机未检测到 HTTP(S)/ALL_PROXY 环境变量。只查询公开 Git 引用，没有私有数据。
- [私有 Git 只读诊断 #38979880648](https://github.com/chaonpu/Stock/actions/runs/38979880648)：在 Stock 私有 Actions 内以 Job 自带的短期 `GITHUB_TOKEN`、最小 `contents:read` 权限，仅访问私有 `chaonpu/Stock.git` 的 Git **HEAD 引用**；默认与 HTTP/1.1 各 3 次，总共 **6/6 通过**，单次约 1.06–1.23 秒。引用内容及凭据没有写入日志，也没有执行私有源码检出。
- [私有浅层 Git 对象诊断 #38980011136](https://github.com/chaonpu/Stock/actions/runs/38980011136)：先做另外 6 次私有引用查询（**6/6 通过**），然后在 Windows `RUNNER_TEMP` 以**临时裸仓库**分别运行默认和 HTTP/1.1 的 `git fetch --depth=1 --no-tags`，结果 **2/2 通过**，分别约 **8.69 秒 / 10.70 秒**。该步骤下载私有 Git 对象，但仅保存在同一私有 Windows Runner 的临时目录，**不创建源码工作树，不展示文件或提交 SHA，不复制到公开 RUNNER**，目录随诊断结束删除。
- 上述只证明这些单次诊断**当时**都成功，不能据此宣布网络抖动根治。既往 [Windows 环境检出失败 #38978705490](https://github.com/chaonpu/Stock/actions/runs/38978705490) 的连续三次 `github.com:443` 超时仍是事实；采样未显示强制 HTTP/1.1 优于默认协议，**暂不修改正式策略 Git 网络配置**，也不启用额外定时重试。
- 仅人工诊断入口：私有 `chaonpu/Stock/.github/workflows/windows-host-prereq.yml`（公开 Git/主机依赖）、`private-git-transport-audit.yml`（私有 Git 引用、临时裸仓库浅层抓取）、`windows-environment-audit.yml`（带源码检出的工具链全验收）。三条工作流均已经移除临时 `push` 触发，无定时触发；工具不读取生产 database、不执行策略、不发送邮件。
- **安全限制保持**：生产登记与派发仍关闭。仅当未来专门的 Windows 策略测试完成源码检出、Git Bash/Node 初始化、受控计算、行情字段核验以及带 RUNNER 来源回执的私有 database 校验后，才考虑引入正式执行和自动补偿。不得以本轮 Git 引用/浅层抓取成功冒充生产策略端到端成功。

## 2026-10-17 Windows A/US 正式策略主机运行条件验收（无任何业务副作用）

- 私有 Stock 新增 [环境验收工具](https://github.com/chaonpu/Stock/blob/main/windows_strategy_environment_audit.py) 与手动工作流 `windows-environment-audit.yml`。第一次 [Run #38978705490](https://github.com/chaonpu/Stock/actions/runs/38978705490) 检出私有 Stock 时 `github.com:443` 连接连续超时而失败，未进入 Python/Node 依赖验收；这属于之前已见的**间歇性 Git HTTP 连接问题**，不能归咎于策略代码。
- 为不依赖私有源码检出，新建仅手动的 `windows-host-prereq.yml`：不读取业务源码或 database，不使用密钥，不执行任何股票行情 HTTP GET，只验证公开域名 TLS、Windows 程序、隔离临时 Python venv 与六项策略依赖（临时安装后自动清理）。[Run #38979028378](https://github.com/chaonpu/Stock/actions/runs/38979028378) 成功：Python 3.14.8、Git 2.56.0、全部六项依赖安装成功（约 41 秒），九个公开域名 TLS 握手成功；初始 PATH 未配置 Node/Bash。
- 按正式工作流原样增加 **Git Bash 路径初始化**和 **`actions/setup-node@v7` 设置 Node 22** 再验收。[Run #38979221960](https://github.com/chaonpu/Stock/actions/runs/38979221960) 以 **success** 完成：Git Bash 5.3.15、Node 22.23.3、Python 3.14.8、Git 2.56.0；全部六项依赖在临时 venv 再次安装成功（约 34 秒），九域名 TLS 成功。说明主机和生产初始化工具链满足当前合成安装要求。
- 上述两个 no-checkout 工作流不代表真正供应商行情字段/API 可用，不证明 Windows Python 3.14 与云端 Python 3.13 的选股结果一致，不证明持仓/账本 Git 事务完整，也不能覆盖**私有 GitHub 检出时断时续**的故障。两条工作流的一次性 push 触发已经移除，只保留人工 `workflow_dispatch`，未新增定时巡检或策略派发。
- 下一步应优先验证 Windows 到 `github.com` 的私有 Git HTTPS 稳定性，以及 Stock 正式业务程序在 Windows 隔离环境下的**只读/模拟计算**条件；任何真实 database 推送、邮件、自动补算、APK 发布仍被禁止。正式生产任务的所有派发开关保持关闭。

## 2026-10-17 Stock RUNNER 标记任务的发布前行情结果校验（Windows 合成验收已通过）

- 私有 Stock 新增 `private_postrun_source_gate.py`：仅对显式包含 `task_id` 的未来协调任务，在运行器产生 **Run ID 对应开始标记**后，读取本次生成的 A股 / 美股正式结果。校验文件更新时间晚于本次标记、实际生成时刻、`as_of` 与任务日期一致、顶层正式状态、核心行情完整日期/覆盖/缺失/收盘条件、A股概念与流通市值及证券状态覆盖、美股行情失败和挂牌目录状态。失败直接阻止 `database_ledger_sync.py`、账本投影和私有 Git 提交，且不输出持仓/报价详情。
- Stock A/US 现有正式工作流的旧式 `Reconcile verified ... ledger projection` 会在运行策略前推送 database。已对**带 task_id** 的协调任务跳过该旧提前写入步骤，避免新校验失败后账本却已变动；**不带任务标记的现有生产任务不受影响**。原 GitHub 工作流发布机制、自动邮件及原交易规则不变。
- 校验只以策略执行过程中已生成的结果为依据，不保证独立实时验证行情源，也不证明运行前的供应商状态；未来策略派发仍需要单独完成权威收盘数据与供应商独立就绪证明。 `upstreamProviderIndependentlyVerified=false`、`preDispatchQuoteReadinessVerified=false`、`privateDatabaseBusinessVerified=false`、`automaticRetryAuthorized=false`，不会用该结果写 `verified`。
- 历史 [Stock #38976761436](https://github.com/chaonpu/Stock/actions/runs/38976761436) 和 [#38976852825](https://github.com/chaonpu/Stock/actions/runs/38976852825) 曾因 Windows → GitHub HTTPS 443 失败而未执行测试，故障仍保留为历史记录。
- **网络恢复后 [Stock #38978211786](https://github.com/chaonpu/Stock/actions/runs/38978211786) 成功**：`ziwuxilv-stock-win` 的 TCP 探针、私有 `actions/checkout` 与 `python -m unittest` 实际执行通过，日志 `Ran 86 tests in 10.195s`、`OK`。包含 A/US 源行情摘要、同次结果时间戳、正式状态、完整性、database 写入顺序及私有执行来源合成测试。未触发任何真实生产策略、数据库推进或 APK 构建。
- 合成测试所用临时 push 路径已删除，私有核验入口恢复为**仅人工启动**。保留检出前网络连通检查，不添加定时复核、后台轮询或自动重试。
- 本次仅证实自托管 Runner 的 `github.com` 检出及合成 Python 测试恢复正常，**不代表正式 Windows 策略的行情供应商、全部依赖和真实入账路径已经验收**。账户额度状态、最终 `verified` 自动升级和全链真实调度仍待验证；生产锁保持关闭。

## 2026-10-17 交易日历与私有账本“双门禁”验收

- RUNNER 官方休市表、提前收盘时间检查已接入 `scheduler.production_dry_run`。若手动指定交易所休市日/尚未到收盘时刻，`routeRecommendation` 会强制变为 `hold`；不允许单凭缓存的“云端额度可用”建议忽略日历错误。有效日期也只能得到**路由建议**，绝不授权真实派发；跨越公开日历覆盖年（2026）直接 hold。
- 私有 Stock 新增 `private_market_readiness.py`，对已有 private database 快照/账本一致性做不泄露持仓的只读分类：`hold_existing_same_date_pair_origin_unverified`、`hold_newer_ledger_already_exists`、`hold_upstream_market_data_not_attested` 或 `hold_private_pair_unavailable`。无论哪种结果，**既有账本都不构成行情数据源就绪证明**，所以 `upstreamQuotesVerified=false`、`dispatchAuthorized=false`、`automaticRetryAuthorized=false`。已接入 Stock 手动诊断入口，不会作为正常自动工作流运行。
- [RUNNER 线上只读验收 #38976033933](https://github.com/chaonpu/Runner/actions/runs/38976033933) **142 项安全测试通过**，并通过实际 GitHub API 读取到公开生产登记表 0 条、A股/美股日历候选均为 `2026-10-16`、`publishedScheduleMatched=true`、`scheduledSessionClosed=true`、`exchangeSessionVerified=false`、`marketDataReady=false`、`routeRecommendation=deferred`、`dispatchAuthorized=false`。只证明发布日历与登记 API 正常，不证明 Stock 私有数据库在生产环境的实时行情准备状态。
- [Stock Windows 安全回归 #38975711719](https://github.com/chaonpu/Stock/actions/runs/38975711719) **75 项测试通过**，不读取真实账本，无策略执行/无 database 写入。两处一次性 push/线上联通验收步骤在通过后均已移除，不添加定时任务或轮询。
- **剩余阻断**：尚无 Stock 各供应商行情源的可审计收盘数据覆盖/完整性契约；尚未独立证明 GitHub-hosted 可免费执行额度及 Windows 正式策略的运行条件；正式调度登记→派发→同任务运行→database 私有回执→`verified` 的真实业务闭环仍未上线。请勿将安全单测、日历已收盘、存量账本一致三者任一项误当正式入账成功。

## 2026-10-17 交易所官方 2026 年日历校核（只读安全门）

- 在 `config/exchange_schedule_2026.json` 存储按交易所公告整理的 **2026 年**公开普通股票休市与提前收盘快照：A 股同时对照 [上交所 2026 休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/) 与 [深交所 2026 通知](https://www.szse.cn/disclosure/notice/general/t20251222_618087.html)；美股同时对照 [NYSE 2026 日历](https://www.nyse.com/trade/hours-calendars) 与 [Nasdaq 2026 日历](https://nasdaqtrader.com/Trader.aspx?id=Calendar)。这是**截至 2026-10-17 核对的公布安排**，不构成临时休市实时证明。没有任何外部市场 API 令牌、调度任务或后台网络拉取。
- `scheduler.exchange_schedule` 将现有北京时间/纽约时间候选提升为“2026 年公开日历候选”：周末、法定休市日自动跳过；美股 11 月 27 日与 12 月 24 日按纽约当地 **13:00** 提前收盘处理；普通交易日 A 股按 15:00、美股按 16:00，自动遵循时区夏令时变化。
- 与真正派发证明严格分离：输出 `publishedScheduleMatched`、`scheduledSessionClosed`、`scheduledClose`、`calendarStatus`；`exchangeSessionVerified=false`、`marketDataReady=false`、`dispatchAuthorized=false`、`productionReady=false` 保持不变。即使日期属于公布交易日并已到计划收盘时点，也不能证明临时停市没有发生、行情源已完成收盘写入、选股结果有效或实际 database 处于可执行状态。
- **2027 年及其他未纳入公开快照的年份一律返回 `hold_calendar_coverage_unavailable`**，不能沿用上一年日历，必须先从交易所公告更新并通过回归。手动日期覆盖同样不得绕过休市与未收盘核验。
- [RUNNER 安全回归 #38975524871](https://github.com/chaonpu/Runner/actions/runs/38975524871) 已执行 **141 项测试通过**（含春节与国庆长假、NYSE 感恩节/圣诞节提前收盘、纳斯达克休市、纽约夏令时、2027 年范围失效、错误日历及无副作用门禁）。没有执行真实策略或更新账本。

## 2026-10-17 第一次线上只读 API 联通验收成功（一次性）

- [RUNNER #38974929403](https://github.com/chaonpu/Runner/actions/runs/38974929403) 利用已有 `validate.yml` 安全测试执行了一次**真实 GitHub REST Contents 读取**。新增的一次性 `scheduler.live_readonly_acceptance` 验证了线上 `state/tasks.json` 的结构和原始 SHA、两个市场的自动日期、既有运行状态查询及只读路由判断，未访问私有仓库内容、未更新任务文件、未派发任务。
- 实际日志：`live_registry_read_only_confirmed`、`registryApiRead=true`、`taskCount=0`；A股、美股日期候选均 `2026-10-16`，已登记任务均 `not_registered`，路由均 `deferred`，`dispatchAuthorized=false`；未能证明剩余额度或 Windows 正式策略兼容性，因此不使用云端或本地生产执行路径。
- 公开安全单测 **125/125 通过**。成功核验后**已从 `validate.yml` 移除临时线上验收步骤**，因此以后代码提交不会自动做实时路由巡检；仍无 5 分钟 schedule、无后台 watcher。保留 `scheduler/live_readonly_acceptance.py` 作为可复现的受限只读诊断程序，不授权生产操作。
- 当前 GitHub 连接没有直接启动 `workflow_dispatch` 的能力；本次并**非手动执行 `production-dispatch.yml` 本身**，不能混淆其真实运行验收。即便日后手动成功运行，该入口仍固定拒绝写入、派发、自动补算。下一步优先验证权威交易日历、可核验余额/Runner 执行环境和正式业务副作用，再考虑放行任何一项生产操作。

## 2026-10-17 日期输入改进：默认市场时钟自动候选

- [production-dispatch.yml](../.github/workflows/production-dispatch.yml) 的 `business_date` 已从**必填**改为**可选空值**。正常人工发起无需填写，`scheduler.market_clock.resolve_date` 使用 GitHub Runner 当前 UTC 时间与官方时区数据库转换成 `Asia/Shanghai` / `America/New_York`，依当地市场名义收盘时间（A股 15:00；美股 16:00，NY夏令时自动处理）计算最近已结束的工作日候选。周末和盘中则回退到上一个工作日。
- 手动填写 `YYYY-MM-DD` 仍允许作为**只读异常复核覆盖**，不是策略补算或派发权限。输出 `dateMode`、`marketTimezone`、`marketLocalClock`、`businessDate` 和 `exchangeSessionVerified=false`；因为仅凭周一至周五不足以确认交易所节假日、临时休市、提前收盘或账本实际数据，日期仅为安全检查候选。真实派发前必须引入**权威交易日历 + 交易所休市时刻 + 数据就绪证据**的单独验证，不得直接使用当前日期或此候选放行策略。
- 同时修复生产入口 Workflow 的六个环境变量 GitHub 表达式前多余的反斜杠，确保 `GH_TOKEN`、任务及日期输入被正常传递。`scheduler.production_dry_run` 仍强制生产开关为 false，绝无 dispatch/PUT。
- [RUNNER 安全测试 #38974308079](https://github.com/chaonpu/Runner/actions/runs/38974308079) **121 项通过**，包含北京时间周末、A股收盘前、纽约标准/夏令时、手工覆盖和失败闭锁。
- Stock `private_coordinator_link.py` 在 RUNNER 输入 `business_date` 为空时保留跨 Run ID 只读比对，但**不会**把 UI 没提供的有效日期当成已独立验证；`taskInputsIndependentlyVerified=false`、`businessVerified=false`、`safeToRetry=false`。跨仓完整生产核验仍未建立。

## 2026-10-17 新入口：正式调度仅开放只读安全验收

- 已创建 `.github/workflows/production-dispatch.yml`（仅 `workflow_dispatch`、管理员 `chaonpu`、`main`；全 Job `permissions: contents:read`）。当前仅允许 A股、美股业务日期的**只读安全门**，没有真实 `dispatch`、无登记表 PUT、无 database 写入、无 APK 发布、无定时任务。
- `scheduler/production_dry_run.py` 先验证自身 GitHub workflow/ref/actor/首次 Run、完整日历日期、任务白名单及**仍关闭**的全局和单任务派发开关。使用只读 `GH_TOKEN` 经 GitHub Contents REST GET **实时**获取公开任务登记，然后复用 `scheduler.preflight` 做只读账户额度和现存运行检查；即使路由建议为 cloud，也强制输出 `dispatchAuthorized=false` 等全关状态。
- `scheduler/production_store.py` 修复 GitHub Base64 内容换行兼容，仅剔除 CR/LF，不接受其它非法字符；已加入回归测试。
- [Runner 安全测试 #38973465844](https://github.com/chaonpu/Runner/actions/runs/38973465844) **116/116 通过**。这是 GitHub-hosted 对代码和工作流边界的安全验收，**不是**新入口真实手工执行、也**不是**生产执行端到端验收。
- Stock 双侧核验器的 RUNNER 输入已同步为 `task` + `business_date`，与 `production-dispatch.yml` 实际输入一致。 [Stock Windows 回归 #38973623420](https://github.com/chaonpu/Stock/actions/runs/38973623420) **56/56 通过**，临时 push 测试触发随后已移除，核验工作流恢复手动-only。
- 真正的 `verified` 必须独立核验完成的协调 Run、Stock Run、实际数据库版本，并处理来源及任务输入缺失；**目前不允许**由只读入口写入 `verified`，也不会自动补算。需要后续单独完成安全副作用验收，才考虑连接派发。

## 2026-10-17 继续跟进：双 Run ID + 私有回执 + 公开登记只读交叉核验

- Stock 正式 A股、美股工作流新增可选 `coordinator_run_id`；当 `task_id` 非空时，两者必须成对有效，否则任务在真正运行策略前终止。回执中新加入 `coordinatorRunId`，并在后续私有 Git 提交验证时检查（GitHub REST 若返回调度输入，还校对输入中的原始 Run ID）。
- 私有 `private_coordinator_link.py` 在手动只读诊断中取得公开 Runner 的 `config/repositories.json`、`state/tasks.json` 和原调度 Run，结合 Stock 目标 Run 及私有提交验证结果，拒绝任何 task ID、两侧 Run ID、仓库、工作流、时间口径或结果错配。公开 HTTP GET 不携带私有 PAT；数据库及持仓绝不复制到公开 RUNNER。
- `private-effect-probe.yml` 已恢复为只有人工 `workflow_dispatch`，当提供准确的 `database_commit_sha` 时再进行双仓核查。**当前 `production-dispatch.yml` 已部署但只读、禁止生产派发**，公开生产注册状态 `state/tasks.json` 仍为空，因此只验证代码契约，没有可供真实生产验收的同源 Run。
- [Stock Windows 合成安全测试 #38972769647](https://github.com/chaonpu/Stock/actions/runs/38972769647)：**56/56 通过**；测试后移除了临时 push 触发，不影响用户既有工作流。
- 即使证据在模拟中完全一致，检查器也不允许写入 `verified`、自动补偿或自动重新派发，维持 `coordinatorVerified=false`、`businessVerified=false`、`safeToRetry=false`。下一步需先部署**默认禁用/只读**的生产调度入口、完成与真实私有策略的一次非重复日期验收，再设计最终写入许可门禁，切勿提前开启正式派发。

## 2026-10-17 私有执行回执：Stock 已完成代码接入与 Windows 合成验收

- Stock 的 A 股 `run-a-share.yml` 与美股 `run-us.yml` 已在**原有 private_publish 阶段**增加可选 `task_id` 回执制作：默认旧任务的 `task_id` 为空时无变化；仅显式 `workflow_dispatch`、首次 Run、非 force、实际源码 SHA 匹配、快照/账本完备、市场日期准确、`latest.json` 市场版本确有变化时才生成一次专属回执。
- `private_run_receipt.py` 将回执置于私有 `ZIWUXILV/database/runner_receipts/<market>/<date>.json`，记录 task ID、Stock Run ID、源码 SHA、database 父提交 SHA 及市场版本/摘要。它与对应变更过的 snapshots/ledger/latest 一起通过**现有同一 Git commit/push**提交。文件使用排他创建，不允许覆盖和同日期重复覆盖。无市场数据更新时不制造只有回执的假数据提交。
- `private_receipt_verify.py` 读取 GitHub Actions Run 的真实元数据及数据库 Git 提交历史，检查原 Run、确切父提交、回执新增、变更集合、提交中的 snapshot/ledger/manifest 内容和摘要。私有只读人工入口可以提供可选 database commit SHA 运行历史核验，要求已配置的 Windows 自托管 Runner、已有 `database` token 及当前 Stock 仓库自身的 Actions 只读令牌；不向公开 Runner 下载私有数据。
- [Stock Windows Run #38971542897](https://github.com/chaonpu/Stock/actions/runs/38971542897) 的**45 项合成回归全部通过**，包括生成/验证 Git commit 树和 Run ID 的不同伪造、损坏、重复/非法日期等情形。测试不调用真实策略，不写入真实 database。
- 先前短暂启用的一次性 push 合成测试入口已取消；`private-effect-probe.yml` 恢复为仅 `workflow_dispatch` 手动入口，无 cron。GitHub 托管分钟不足时，安全测试已验证可在 `ziwuxilv-stock-win` 上运行。
- **还不能称为生产验收完成**：没有新式任务 ID 真实生成的 private database 回执，没有将私有核验结论与公开 Runner 的原预约身份及 Run ID 进行独立可信交叉核对，也没有允许公开 registry 写 `verified`。即使私有提交与 Run 一致，也继续输出 `coordinatorVerified=false`、`businessVerified=false`、`safeToRetry=false`。保持正式派发开关关闭，不重跑历史交易日。

## 2026-10-17 最新验收：全新 Stock 无操作闭环已通过，正式任务防重基础已接入预检

- [Runner 调度 Run 38967023305](https://github.com/chaonpu/Runner/actions/runs/38967023305) 于 2026-10-17 北京时间 05:06 启动，先预约 `pilot:stock:2026-10-18`，再派发私有 [Stock Run 38967039826](https://github.com/chaonpu/Stock/actions/runs/38967039826)。
- 同一调度 Job 的预约、派发、前台监控、完成回写和退出步骤均为 Success。公开 `state/pilot_runs.json` 的新任务为 `completed_success`，此前 `pilot:stock:2026-10-17` 保留。新任务已成功验证全部无业务副作用闭环。
- 新增纯函数 `scheduler/production_registry.py`：严格验证 A/US/ETF 的日期任务 ID 与 Android SHA、阻止重复预约、将目标 Run ID 仅绑定一次、核验目标仓库/工作流/分支/事件/Run Attempt。失败或身份不可信时拒绝自动重派。
- 新增 `tests/test_production_registry.py`，并在只读 `scheduler.preflight` 中检查 `state/tasks.json` 的已有任务：未完成/不可信记录为 hold，已可信标记 `verified` 的任务跳过。
- **历史阶段说明（已由下述阶段更新）**：当时仅有纯状态契约。当前已有尚未接入正式派发的 CAS 存储模块与 Stock 私有核验代码；仍没有生产级自动完成核验、正式派发或自动补偿。不能将模拟 `verified` 测试误解为真实账本验证。
- `config/repositories.json` 的全局和逐任务正式派发开关仍全部关闭；Stock 的 Windows 生产路径未验收，APK 发布审批保持独立。以上全部改动均未运行真实策略、未写入 database、未发送策略邮件、未构建或发布 APK。

## 2026-10-17 第一阶段实施：生产登记存储 + 私有入账只读核验（未启用正式派发）

- 公开 `Runner/scheduler/production_store.py` 新增 `state/tasks.json` 的 GitHub Contents GET/PUT 存储层：每次 PUT 使用读取到的 blob SHA，拒绝无可信 `workflow_dispatch` 上下文、非拥有者、私自改写其他任务与根元数据、重复登记、Run ID 替换、虚假 `verified` 状态。网络异常/写入结果未知**不自动重试**。调用它的**正式生产调度 Job 尚不存在**，因此没有真正生产任务被写入。
- 公开状态管理 `production_registry.py` 和只读预检 `preflight.py` 维持此前约束。条件提交是一层并发保护，后续接入仍须按任务设置 `concurrency` 并检查现有正式策略与原定时来源，不能单凭 SHA 完成跨仓库原子事务。
- 私有 `chaonpu/Stock/private_effect_probe.py` 新增 `latest.json`、快照索引链、快照字节、独立市场账本、不可变归档及其哈希关联的只读检查；`.github/workflows/private-effect-probe.yml` 仅供 `chaonpu` 从 main 手动运行，并通过原有 `secrets.database` 在 Stock 私有环境中读取。不会推送 database 或输出私有持仓。
- `private_effect_probe.py` 的结果**绝不构成一次运行的来源证明**，只可报告数据一致而来源未知；没有可信 `task_id` / Run ID / 数据库版本写入回执之前，即使账本日期相符，仍保持 `businessVerified=false`、`safeToRetry=false`。需先在私有侧建立可审计、与实际 Git 提交绑定的运行回执，才可接入 `verified` 状态。
- 公开 RUNNER [安全测试 #38969991137](https://github.com/chaonpu/Runner/actions/runs/38969991137) **106 项通过**。Stock 的合成核验作业 [#38969769464](https://github.com/chaonpu/Stock/actions/runs/38969769464) 和 [#38969885219](https://github.com/chaonpu/Stock/actions/runs/38969885219) 在分配 Runner 前失败（无步骤、runner_id=0），测试代码尚未由 GitHub Actions 成功执行。已经移除 Stock 新核验工作流的自动 push 测试触发，暂仅手动按需运行，以避免额度不可用时重复失败。
- **当前仍为完全安全锁定**：`dispatchEnabled=false`、全部 `enabledForDispatch=false`、生产队列为空、不改现有 A/US 策略/邮件/账本/APK/旧定时，仍无五分钟轮询。

## 当前有效设计：调度即监控，完成即退出（取代全部定时巡检）

本节是**当前实施规则**；下文关于“五分钟定时复核”的文字只是迁移历史，不再代表当前配置。

- 已逐一检查 `Runner/.github/workflows/` 的全部八个工作流：**无任何 `schedule`/cron 触发器**。
- 正常路径：手动启动 `stock-noop-pilot.yml` → 原子登记测试日期 → 唯一 `workflow_dispatch` 派发私有 Stock 无业务 canary → 保存返回的准确 Run ID → **同一个 GitHub-hosted Ubuntu Job** 每约 30 秒 GET 该 Run 的实际状态 → 完成后先核验目标仓库、工作流、事件、分支、Run ID、结论，再原子写回 `completed_success` 或 `completed_failure`，**随后退出，没有常驻后台轮询器**。
- 监控最长约 50 分钟，Job 上限 60 分钟。GitHub API 连续失败、任务长时间排队、Runner 离线、状态或身份不可信时停止并保留同一 Run 的登记，**不重派、不重算、不修改正式账本**。
- `stock-noop-pilot.yml` 的 `mode=monitor_only` 仅供有必要时**手动**恢复原 Run 的监控，绝不重新执行业务；`reconcile.yml` 也已改成只有 `workflow_dispatch` 的手动异常核对入口，不会自行启动。
- 原调度 Job 的收尾步骤现在使用 `scheduler.pilot_finalize --date <本次测试日期>`，只处理本次任务，不顺带修改其他日期的状态。
- [Runner 安全测试 #38965688218](https://github.com/chaonpu/Runner/actions/runs/38965688218)：**76 项通过**，含失败/取消/超时、断网、误匹配 Run、按日期精确完成、避免重复派发。历史成功 Stock Run `38961249562` 已通过只读实时轮询验证（[Run 38963736129](https://github.com/chaonpu/Runner/actions/runs/38963736129)）。
- **最新验收更正**：全新无操作任务的派发、等待、回写、退出已在 Run `38967023305` 通过；这**不等于**正式策略、数据库入账、APK 发布流程已上线。
- **正式 A 股／美股及 APK 调度开关仍为关闭**。不要把无操作 canary 运行 Success 视作实际策略、数据同步或 APK 发布成功。


## 最新生效方案：按需前台监控，不设置任何周期性复核

**以本节为准；下方旧验收日志保留历史记录，涉及“每五分钟 cron”的做法均已废弃。**

- 原 `.github/workflows/reconcile.yml` 的 `schedule: cron "*/5 * * * *"` **已删除**；现在只有 `workflow_dispatch` 手动异常恢复入口，平时不运行。
- Stock 无操作演练 `.github/workflows/stock-noop-pilot.yml` 选择 `mode=dispatch` 时，先原子预约，派发精确私有 Run ID，在**同一 GitHub-hosted Job**中每 30 秒轮询完成状态，随后通过 `pilot_finalize.py` 将真实结果写回 `completed_success`／`completed_failure` 并退出。
- `mode=monitor_only` 可在 Job 异常结束后手动针对登记过的 Run ID 恢复监控，**绝不新建或重复派发私有工作流**。正常任务不需要任何后台轮询器。
- 对意外取消、网络 API 故障、运行器长期离线或监控时限（当前无操作演练为 50 分钟）：立即停止监控，保留原任务记录供人工检查；不能把未确认结果记为成功，更不能立即重复派发。
- 公开 Runner 的正常源码安全测试继续由 `validate.yml` 的代码提交触发，这不是任务定时监控。
- **当前仅 Stock 无操作 canary 接入该前台监控。A/US/Android 正式自动派发仍关闭**；待真实业务去重、核验、安全恢复通过后才可复用。

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
- [x] Stock 无操作演练已实现先登记、Run ID 回写与前台监控；不使用五分钟 cron。正式生产任务的自动恢复/补算仍待验收。

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


## 2026-10-17：Stock 无操作调度的持久化验收基础已部署

使用现有 `CHAONPU_ACTIONS_READ_TOKEN`（可派发私有仓库，但仍不包含公开正式发布仓库）及 `Runner` 自身 `GITHUB_TOKEN: contents:write`：

1. 手动 `Runner/.github/workflows/stock-noop-pilot.yml` 输入 YYYY-MM-DD 测试日期；
2. 在 `Runner/state/pilot_runs.json` 中先写入不可自动覆盖的 `pilot:stock:YYYY-MM-DD` 保留记录，GitHub Contents API 使用原文件 SHA 做条件提交；
3. 仅派发 `chaonpu/Stock/runner-dispatch-canary.yml` 无操作测试工作流；
4. 有明确返回时再补全实际 `workflow_run_id`；204、网络超时、权限异常或写入冲突均保守记录或保留原始预约，**不会自动重新派发**；
5. `reconcile.yml` 继续每五分钟读取 `state/pilot_runs.json` 的可追踪 Run ID，并使用私有只读 API 验证目标仓库与工作流的 `queued/running/success/failure` 状态；即使 Success 也只标记“测试完成”，**绝不视为策略或账本有效**。

云端测试 Run [38960499365](https://github.com/chaonpu/Runner/actions/runs/38960499365) 成功：**55 项测试通过**，覆盖先登记后派发、相同日期拒绝重复、HTTP 响应未知不自动重试、错误目标隔离、实际 Run ID 的只读核查。

**仍待首次手动实测整个先预约→无操作派发→保存 ID 流程**：GitHub 连接当前可以更新仓库代码和查询 Action，但未提供可用的通用 `workflow_dispatch` 调用，因此不声称这次新增的手动 pilot 已真正执行过。必须由拥有者在 Runner Actions 页面手动启动一次后检查其真实日志和 `state/pilot_runs.json` 是否成功写入。

以上**不是** A/US 策略的生产调度上线；`dispatchEnabled=false`、各业务 `enabledForDispatch=false` 未改。Stock Windows 只完成真实依赖与离线回归，暂不自动执行会写入 database 的完整策略。

## 2026-10-17：Stock 无操作跨仓库调度及完成回写真实闭环

用户手动发起 `Runner` [Run 38961235915](https://github.com/chaonpu/Runner/actions/runs/38961235915)，其向私有 `Stock` 派发的 [Run 38961249562](https://github.com/chaonpu/Stock/actions/runs/38961249562) 已真实 **Success**。公开 `state/pilot_runs.json` 保存了任务 `pilot:stock:2026-10-17`、两侧准确 Run ID 和唯一 canary 标识。

在已通过 **64 项安全测试**后，为现有五分钟 `reconcile.yml` 增加了一个独立的、仅能修改无操作 pilot 状态的 Job，使用 `Runner` 自身 Contents write 和已存在的私有 Actions read Token。只有核实目标是 `chaonpu/Stock`、`runner-dispatch-canary.yml`、`workflow_dispatch`、`main`，且 Run ID 对应的结果为 `completed/success`，才能将公开登记状态经 GitHub SHA 条件更新为 `completed_success`。未成功、API 故障、权限缺失和并发冲突均拒绝自动推进；不调用正式策略、数据库和 APK 发布工作流。

通过一次临时同仓库触发，真实运行 [reconcile #38961955707](https://github.com/chaonpu/Runner/actions/runs/38961955707)，两个 Job 全部 Success。完成状态写入日志显示 `updated=1`、`dispatches=0`；最终状态文件实际为 `completed_success`，`runId=38961249562`。这证明**云端按需复核 + 状态回写**正常。测试用临时工作流 `trigger-reconcile-once.yml` 已删除。

**注意：上面的复核 Run 为 `workflow_dispatch`，而非五分钟 `schedule` 事件。** 查询近期 Runner Action 历史时，未看到 `schedule` 触发的运行。因此五分钟 cron 的实际送达和定时持续性**尚未验收**，不能把配置存在或手动运行成功表述为定时机制已稳定运行。正式策略的自动调度仍全部关闭。

