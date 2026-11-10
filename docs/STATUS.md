# 当前项目状态

**更新日期**：2026-11-10
**整体状态**：T003 和 T005 已合并至 main；T004 已获得手机热点基础收发的用户验证但 G1 人工项目未补齐；T006 Python CLI 安全续传、T007 分发、T008 受控并发、T009 UDP 发现与 T010 实验驱动均已合并。T011 的只读数据分析、静态图表和抓包方法正在实现；真实 G4 网络实验仍为 NOT_RUN。

## 事实（已确认）
- GitHub 仓库：`cppppercy/lan-distribution-lab`，默认 main；最初为空。
- 用户环境：Windows、32 GB 内存，手机与平板两台移动设备，家庭 Wi-Fi 或手机热点。
- 用户确认局域网基本连接没有问题；项目专用服务的连通性/性能尚无运行记录。
- 编码协作：Codex 优先、DSH 备用，ChatGPT 制定需求和审核。
- 已完成文档：README、REQUIREMENTS、AGENTS、TASKS、DECISIONS、STATUS（以仓库实际提交为准）。
- 项目代码：最小 Python 包、仅本机监听的 Uvicorn 启动入口、`GET /api/health`、本机配对 CLI、`POST /api/pairings`、会话撤销、SQLite 状态与 pytest：**IMPLEMENTED**。
- 文件上传/下载核心、SHA-256、元数据和传输摘要：**IMPLEMENTED（T003-B）**；HTTP Range 与 Python CLI 下载续传：**IMPLEMENTED（T005/T006，已合并）**；多客户端顺序/并发分发控制与本机运行记录：**IMPLEMENTED（T007，已合并）**；受控并发调度 controlled 与 SQLite 迁移：**IMPLEMENTED（T008，已合并）**；UDP 局域网发现与 `discover` CLI：**IMPLEMENTED（T009，已合并）**；控制器侧实验计划、CSV/JSON 原始数据与描述统计：**IMPLEMENTED（T010，已合并）**；T010 只读校验、SVG 图表/Markdown 分析与抓包方法：**IMPLEMENTED（T011，待审查）**；P2P：**NOT_IMPLEMENTED**。
- T002 设计文档：文件 API 契约、配对/会话威胁模型、存储/版本原则、测试矩阵：**COMPLETED（设计，不是业务实现）**。
- GitHub Actions 工作流：**IMPLEMENTED 且 PASS**。T001 分支推送与 PR #1 均在 Python 3.12 成功执行 pytest（25 秒、27 秒）。
- 真实手机/平板测量、断线实验、性能结果：**NOT_RUN**。

## 当前任务
- **T001 — DONE**：最小工程初始化、健康接口、自动化测试、CI 配置和启动说明已按任务边界完成。完整证据见下方记录与 `docs/TASKS.md`。
- **T002 — DONE**：API 契约、安全威胁模型和测试方案已完成，且未越界实现文件业务。详见 `docs/API_CONTRACT.md`、`docs/THREAT_MODEL.md`、`docs/TEST_PLAN.md`。
- **T003-A — DONE**：配对码、会话、撤销、scope 与存储基础实现完成并完成有界请求读取补修。
- **T003-B — DONE（实现与本地测试）**：受控上传/下载、SHA-256、SQLite 文件/任务摘要与安全测试已完成。
- **T003 — DONE**：T003-A/B 及最终验收补修已合并至 main。
- **T004 — IN_PROGRESS**：Vue Web、同源静态托管、Cookie 集成、显式私有 IPv4 LAN 启动及自动选址启动脚本已实现；手机热点基础互传已验证，平板、SHA-256 对照和断线等 G1 人工项目待补验。
- **T005 — DONE**：单 Range、强 ETag If-Range、范围任务语义和自动化边界测试已合并至 main。
- **T006 — DONE**：Python CLI `.part`/无秘密状态、Range/If-Range 恢复、强响应校验、SHA-256 发布、自动化故障测试与 2026-11-10 审核补修已完成；PR #6 已合并至 main（合并提交 `dbe69d116fb6ecf15b0b7d1c9dae842d3b2c5a55`）。
- **T007 — DONE**：`python -m lan_distribution_lab distribute` 控制器、sequential/parallel 策略、每客户端独立凭据与独立输出、运行级 JSON 与独立 SQLite 分发记录、审核补修已完成；PR #7 已合并至 main（合并提交 `155462e5324f915645f490b02847fa3a5e46addf`）。
- **T008 — DONE**：controlled 固定并发策略（`--max-concurrent`）、FIFO 排队、等待/执行/批次计时拆分、JSON schema v2、T007 SQLite 原地迁移与 11 项新测试已完成；PR #8 已合并至 main（合并提交 `fa698871e12f650bea5a1b32da322b217aa662e4`）。
- **T009 — DONE**：可选 UDP 局域网发现 responder、`discover` CLI、request_id/来源 IP/版本校验、去重排序、限速与 14 项测试已合并至 main（`1d2c131ecc71ed3e8d49428e7a51318993681ee1`）；真实广播仍 NOT_RUN。
- **T010 — DONE（已合并）**：`experiment` CLI 以有界、确定顺序复用既有分发，保留每轮原始 JSON 与 CSV/manifest，记录失败/取消/中断且不写凭据。整体状态仅在全部计划 trial 成功时为 `completed`；失败或取消会返回非零 CLI 退出码而不删除导出。真实手机/平板/无线 G4 数据仍 NOT_RUN。
- **T011 — IN_PROGRESS（实现与本地自动化完成，待审查）**：`analyze` 只读校验 T010 manifest/CSV/原始 run，按可比条件输出描述统计、SVG 和 Markdown；可选抓包输入仅是本地脱敏摘要，未读取 PCAP 或执行真实抓包。真实 G4 数据仍 NOT_RUN。
- 下一关卡：T011 审查和合并后才可进入 T012；T004/G1 与 T010/G4 的剩余真实设备项目仍须如实补验。

## T011 验证记录（2026-11-10）

```text
任务：T011 — 网络实验分析、性能图表与抓包证据整理
基线 SHA：3fb1eceec16f92cc95c5fa96e910fa331cb57d4d（PR #10 合并后的 main）
实际代码变化：新增标准库 analysis.py 与 `python -m lan_distribution_lab analyze`。它只读接受 schema-v1 T010 目录，核对 manifest、trials.csv、clients.csv 与存在的 DistributionRun JSON；要求每个计划 `round × file_id × strategy` 恰好一次，原始记录唯一、严格位于对应 trial 目录，逐客户端核对 ID、状态、字节/时间和完整性结果；拒绝重复 ID、NaN/∞、零时长完整 trial、状态/计数/文件 ID/大小/SHA-256 矛盾和缺失原始记录。按文件版本/大小、客户端数、环境、超时、重试、并发配置和脱敏服务器身份分组，生成 analysis.json、Markdown 与静态 SVG；失败/取消不被静默删除。不同服务器不会合并为 controlled 上限对照，输出不回显服务器地址。可选 packet summary 仅接收受限大小/数量、字段和值白名单 JSON，不读取 PCAP、不监听/上传网络流量；自由文本、认证信息、Cookie、私人路径及不安全标识符会被拒绝。分析输出目录若位于输入目录内（含绝对、相对或可解析的符号链接别名）会在创建前拒绝。
本地验证：PASS — `python -m compileall -q src tests`；`tests/test_analysis.py` 20 passed、1 skipped（符号链接别名路径因当前 Windows 账户无创建符号链接权限而跳过；绝对/相对路径覆盖已通过），覆盖标准目录、三策略统计、MiB 换算、Jain 指数、失败/取消、不同条件与不同服务器分组、计划/原始记录/客户端篡改拒绝、输入只读、抓包摘要白名单、自由文本/超大输入/过多 observation 拒绝与无秘密报告。完整 Python 套件因本机工具会丢失长命令尾部输出而按互斥文件组实际完成：analysis/auth/LAN/experiment 59 passed, 1 skipped, 1 warning in 26.92s；distribution 37 passed in 16.75s；file API/health/resume/UDP/Web 75 passed, 1 skipped, 1 warning in 13.91s（合计 171 passed, 2 skipped, 2 warnings）。两项 skip 均为当前 Windows 测试环境不允许创建符号链接。`python -m pip check`：No broken requirements found；`npm --prefix web run test`：2 passed；`npm --prefix web run build`：通过；`git diff --check`：通过。
真实设备/网络/抓包：NOT_RUN — 未执行手机、平板、Wi-Fi/热点、校园网、断线、Wireshark/tshark 或真实 PCAP 分析。
已知限制：应用层吞吐不是 TCP/NIC 带宽；Jain 指数只分析完整 trial 成功客户端的应用层完成速率；无脱敏抓包摘要时报告不作包级结论。
```

## T010 验证记录（2026-11-10）

```text
任务：T010 — 可重复网络实验驱动与数据采集
基线 SHA：1d2c131ecc71ed3e8d49428e7a51318993681ee1（PR #9 合并后的 main）
实际代码变化：新增 experiment.py 和 `python -m lan_distribution_lab experiment`。它以固定 round→file_id→strategy 顺序、UUID experiment/trial ID 和客户端/轮次/文件/总 trial 有界配置复用 run_distribution()；一次启动时收集每个逻辑客户端独立的内存凭据，后续轮次复用但不落盘。每轮使用全新的 trial 目录，成功时保留原始 DistributionRun JSON，根目录原子导出 experiments.json、trials.csv、clients.csv，含环境标签、Git/Python/平台、字段单位和描述性统计。整体状态只有全部计划 trial 成功时才为 `completed`；`--on-error continue` 的失败为 `completed_with_failures`，`--on-error stop` 的失败与未开始取消记录为 `completed_with_cancelled_trials`。普通不完整状态会保留全部原始 JSON/CSV/manifest、打印输出目录并让 CLI 返回 1；Ctrl+C 仍记录 `interrupted` 并返回 130。错误文本和凭据不进入导出。
本地验证：PASS — `python -m compileall -q src tests`；`python -m lan_distribution_lab experiment --help`；`tests/test_experiment_runner.py` 12 passed（含 continue 的成功/失败/成功、stop 的失败/取消记录和 CLI 非零退出）；完整 Python 套件在禁用仅影响本机测试的代理变量、使用独立临时目录后按两个互斥测试组完成：认证/LAN/分发/实验驱动 76 passed in 23.86s；文件 API/健康/续传/UDP/Web 75 passed, 1 skipped, 1 warning in 14.14s（合计 151 passed, 1 skipped, 1 warning）。skip 是此 Windows 环境不允许创建符号链接。`python -m pip check`：No broken requirements found。`npm --prefix web run test`：2 passed；`npm --prefix web run build`：通过。`git diff --check`：通过。
CI 补修验证：GitHub Windows runner 曾暴露 `test_controlled_real_sessions_are_independent` 的测试竞态：客户端已读完响应，但服务端流式生成器的 SQLite 完成记录尚未提交。测试现等待最多 2 秒的可观测完成状态，不改变服务端或客户端下载语义；本机连续运行该用例 5 次均通过，完整 `tests/test_distribution.py` 为 37 passed in 17.29s。此补修后新的 PR #10 CI 以 Checks 的最终状态为准。
环境说明：默认 pytest 临时目录遭受权限拒绝（WinError 5），故所有实际 pytest 采用 $LOCALAPPDATA 下独立临时目录和 `--basetemp`；系统 SOCKS 代理使 httpx 本机服务测试误尝试 socksio，因此测试进程临时清除代理变量并设 NO_PROXY=*。两者均为测试环境限制，不是项目功能失败，也未修改系统配置。
真实设备/网络：NOT_RUN — 本次没有要求也没有执行手机、平板、真实 Wi-Fi/热点/校园网、真实断线、性能/吞吐量测试；`local_loopback` 与 `controller_to_lan` 都不等同于真实独立物理客户端。
已知限制：单个实验批次顺序执行，尚无跨进程恢复；统计是应用层描述值而非链路带宽或显著性结论；HTTP 无 TLS；真实无线、Windows ACL/reparse-point 和断电一致性仍未验证。
当前分支、工作区状态、PR：`feat/T010-experiment-runner`；代码提交 `bfcb4b1ee7677d61ad45d9d036e66fdafd7401b2` 已推送，PR #10 已创建且保持 OPEN（不自动合并）。GitHub Actions：PASS — 分支推送 [run 42786443583](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42786443583/job/129728733699) 2 分 13 秒、PR [run 42786500055](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42786500055/job/129728919019) 1 分 31 秒，均在 Windows / Python 3.12 运行 pytest、前端测试和构建通过。此 CI 状态文档更新将触发新的只含文档提交检查，最终以 PR #10 Checks 为准。
下一任务：仅在 T010 审查、合并并保留真实 G4 数据边界后，才考虑 T011；不得把本机结果扩写为真实网络结论。
```

## T009 验证记录（2026-11-10）

```text
任务：T009 — UDP 局域网发现与连接体验
基线分支与起点 SHA：feat/T009-udp-discovery @ fa698871e12f650bea5a1b32da322b217aa662e4（PR #8 合并后的 main）
实际代码变化：
  新增 src/lan_distribution_lab/discovery.py：可选 UDP 发现 responder 与客户端探测。协议为有界 UTF-8 JSON 单请求/单响应（请求/响应各 ≤1 KiB）；请求含协议名、协议版本、type=discover、随机 request_id；响应回显 request_id，并含非秘密 instance_id、service_name、http_port、requires_pairing、features。响应端只接受私有/回环来源、校验协议/版本/request_id，按来源限速并限制跟踪来源数；无效、超大或格式错误的数据包被忽略。客户端以 UDP 源 IP + http_port 构造 URL（不信任正文 URL），校验 request_id/版本/字段边界与私有来源，去重（instance+address+port）、排序并限制响应数/数据包数/总超时，不发送任何凭据。
  __main__.py：serve 新增 --discover/--discover-port/--discover-bind/--service-name（仅显式启用；--discover 要求私有 LAN --host，UDP 绑定与 HTTP 绑定策略分离）；新增 `python -m lan_distribution_lab discover`（--port/--timeout/--broadcast 可重复/--interface/--json），发现超时时提示手动 URL；serve 启动时打印 LAN URL 与发现端口，关闭时 finally 释放 UDP socket。
  scripts/start-lan.ps1：新增 -EnableDiscovery/-DiscoveryPort，默认关闭，启用时传递 --discover 并提示防火墙需人工放行；未改变原有默认行为。
测试：PASS — 新增 tests/test_udp_discovery.py 14 项：loopback 发现与 URL 构造、响应字段/大小/无秘密、非法请求被忽略且服务仍响应、request_id/版本/来源/超大响应拒绝、重复与恶意响应的去重排序、发现请求不含凭据、超时快速返回、UDP 端口冲突与关闭后释放、限速器窗口/来源上限、地址与服务名校验、serve 发现开关与参数校验、CLI 无结果回退提示、JSON 输出与错误退出。
完整回归：`python -m compileall -q src tests` 通过；`python -m pytest`（Python 3.13.7：139 passed, 1 skipped, 1 warning in 41.12s）；`python -m pip check`、`npm --prefix web run test`（2 passed）、`npm --prefix web run build`、`git diff --check` 见下方 CI/验收更新。
真实设备/网络：NOT_RUN — 本机 loopback UDP 测试成功不代表真实 Wi-Fi/手机热点广播发现；Windows 防火墙、校园 Wi-Fi 客户端隔离、多网卡选择、休眠/断网与跨 VLAN 均未实测。
已知限制：UDP 广播只在当前广播域有效；二维码未实现（手动输入 URL 即可）；UDP 发现不提供身份认证，发现后仍需一次性配对码；HTTP 无 TLS；未实现 P2P/公网发现。
当前分支、工作区状态、PR：feat/T009-udp-discovery；PR #9（https://github.com/cppppercy/lan-distribution-lab/pull/9）未合并，HEAD 为 f837a75ba3f8b91c336dbe0ce04a7dd347470357（以远端为准）。
本地提交 SHA：b47353d34af14b73bbb417e209605894667907b8（feat：UDP 发现与 CLI/脚本）、f837a75ba3f8b91c336dbe0ce04a7dd347470357（docs：README/ARCHITECTURE/THREAT_MODEL/TASKS/STATUS/DECISIONS/TEST_PLAN/EXPERIMENTS）。
GitHub Actions：PASS — 上述提交推送后的两次工作流均在 Windows / Python 3.12 成功执行 npm ci、前端测试/构建与 pytest：push [run 42776179530](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42776179530/job/129695085847)（1 分 41 秒）、PR [run 42776198753](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42776198753/job/129695144094)（1 分 47 秒）。本记录自身的文档提交会再次触发工作流，最终状态以 PR #9 Checks 为准，不自动合并。
```

## T008 验证记录（2026-11-10）

```text
任务：T008 — 受控并发调度与实验入口
基线分支与起点 SHA：feat/T008-controlled-concurrency @ 155462e5324f915645f490b02847fa3a5e46addf（PR #7 合并后的 main）
实际代码变化：
  distribution.py 新增 controlled 策略：按 FIFO 顺序提交到 max_concurrent 个工作者的 ThreadPoolExecutor，上限必须为 1..参与客户端数且不超过安全上限；一个任务结束后自动接纳下一个等待任务；sequential/parallel 行为与参数保持不变。新增 _ActiveCounter 记录观测最大活跃任务数，ClientResult 新增 queued_at/wait_seconds，DistributionRun 新增 max_concurrent/observed_max_active；客户端执行时间与排队等待分开记录，批次总耗时仍为运行级 duration_seconds，均使用 time.monotonic()。
  distribution_store.py 支持三种 strategy：T007 旧库原地迁移（重建 distribution_runs 以放宽 strategy CHECK 并保留历史行；distribution_clients 增加 queued_at/wait_seconds 列），重复打开/迁移幂等，不删除数据库、不丢失历史数据。
  __main__.py 新增 --max-concurrent：仅 controlled 有效，缺失/0/负数/超过客户端数/超过安全上限/误用于其他策略时在下载开始前报错；CLI 输出调度配置与观测并发。
  JSON schema_version 升为 2，新增 scheduling 与客户端等待字段；v1 字段语义不变（加法扩展）。
测试：PASS — tests/test_distribution.py 37 passed（T008 新增 11 项）：5 客户端/上限 2 时服务端在途请求计数从不 >2 且实际达到 2；上限 1 严格 FIFO 且等待时间递增；上限等于客户端数时全部并发；参数边界与策略误用拒绝；失败客户端不阻塞等待任务；真实 uvicorn 服务器独立会话与输出 SHA-256；JSON/SQLite 计时与调度字段自洽且无 token/私人路径；T007 旧数据库迁移后写入 controlled 且历史保留；T007 sequential/parallel 与 T006 续传回归。
完整回归：`python -m compileall -q src tests` 通过；`python -m pytest`（Python 3.13.7：125 passed, 1 skipped, 1 warning in 38.63s）；`python -m pip check`、`npm --prefix web run test`（2 passed）、`npm --prefix web run build`、`git diff --check` 见下方 CI/验收更新。
真实设备/网络：NOT_RUN — 受控并发测试使用本机受控 HTTP 服务与真实本机 uvicorn 会话，不是手机、平板或真实 Wi-Fi 的性能实验。
已知限制：仅固定并发窗口，不实现动态窗口/自适应速率/优先级（超出 T008 范围）；观测最大活跃数仅在控制器进程内可靠测量；并发上限 8 与服务端槽位/磁盘限制共同约束；HTTP 无 TLS；Windows ACL 与断电一致性未实测。
当前分支、工作区状态、PR：feat/T008-controlled-concurrency；PR #8（https://github.com/cppppercy/lan-distribution-lab/pull/8）未合并，HEAD 为 003fb46579807fc85b45b868415a824b9cd2f322（以远端为准）。
本地提交 SHA：2a123d07ffdaa1ba4186cc0efa33afbfda6c0dcd（feat：controlled 与迁移）、003fb46579807fc85b45b868415a824b9cd2f322（docs：README/ARCHITECTURE/TASKS/STATUS/DECISIONS/EXPERIMENTS）。
GitHub Actions：PASS — 上述提交推送后的两次工作流均在 Windows / Python 3.12 成功执行 npm ci、前端测试/构建与 pytest：push [run 42772364639](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42772364639/job/129682892998)（1 分 39 秒）、PR [run 42772380585](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42772380585/job/129682941726)（1 分 36 秒）。本记录自身的文档提交会再次触发工作流，最终状态以 PR #8 Checks 为准，不自动合并。
合并状态（2026-11-10）：PR #8 已由用户合并至 main，合并提交 fa698871e12f650bea5a1b32da322b217aa662e4；T008 状态更新为 DONE。
```

## T006 审核补修记录（2026-11-10）

```text
任务：T006 — 审核问题 P0/P1/P2 补修（PR #6）
基线分支与起点 SHA：feat/T006-resumable-client @ dc378f8eb7ae83d0f23dd29947cec175beeef0ad；PR #6 未合并，main 仍为 05474cb。
实际代码变化：
  P0 重定向凭据泄露：客户端默认 opener 改为自定义 HTTPRedirectHandler，任何 3xx 都不跟随；元数据、内容下载与配对码兑换收到 301/302/303/305/307/308 时抛出不含 URL、Location 或凭据的 ProtocolError。保留可注入 opener 的测试接缝，未引入新运行时依赖，未改变后端协议。
  P1 完整 .part 重复下载：发送内容 GET 之前检查续传状态；downloaded_bytes 等于 size_bytes 且 .part 实际大小一致时，直接流式 SHA-256 校验并硬链接发布，不再请求内容；哈希不符则隔离 .part/.part.json 且不发布；零字节文件同样适用，全新空文件仍执行一次 200 下载。
  P2 resumed_bytes 统计：只统计本次成功下载实际复用的旧数据；200/416/版本变化触发隔离重启后归零；真实 Range 续传保留复用字节数。
本地验证：PASS — `python -m compileall -q src tests`；`python -m pytest`（Python 3.13.7：88 passed, 1 skipped, 1 warning in 10.96s，其中 tests/test_resumable_client.py 21 passed）；`python -m pip check`（No broken requirements found）；`npm --prefix web run test`（2 passed）；`npm --prefix web run build`（通过）；`git diff --check`（通过）。新增 12 项测试，其中 8 项使用本地两个不同端口的真实 HTTP 服务：客户端不跟随 301/302/303/307/308，第二端口未收到 Bearer token 或配对码，失败不发布目标文件；其余覆盖完整 .part 免重复下载、空文件完整 .part、完整但损坏 .part 隔离、200/416/版本变化后的 resumed_bytes 归零。
真实设备/网络：NOT_RUN — 本次仅要求自动化测试，未做真实手机、平板、真实 Wi-Fi/热点或断线人工验收；用户计划在主要功能完成后集中人工验收。
已知限制：HTTP 无 TLS，同网段窃听仍可取得 token/内容；Windows ACL、断电一致性与真实无线中断未实测；上传续传、多范围、浏览器后台续传仍不实现。
当前分支、工作区状态、PR：feat/T006-resumable-client；PR #6（https://github.com/cppppercy/lan-distribution-lab/pull/6）未合并，HEAD 为 0a35412e2f25a613cc1b44e95a57365da35c7fa0。
本地提交 SHA：d3ee1fb（fix：client 与测试）、0a35412（docs：STATUS/TASKS/DECISIONS）。
GitHub Actions：PASS — 上述提交推送后的两次工作流均在 Windows / Python 3.12 成功执行 npm ci、前端测试/构建与 pytest：push [run 42735847761](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42735847761/job/129564759996)（1 分 4 秒）、PR [run 42735853416](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42735853416/job/129564778611)（1 分 24 秒）。本记录自身的文档提交会再次触发工作流，最终状态以 PR #6 Checks 为准；不自动合并。
合并状态（2026-11-10）：PR #6 已由用户合并至 main，合并提交 dbe69d116fb6ecf15b0b7d1c9dae842d3b2c5a55；T006 状态更新为 DONE。
```

## T007 验证记录（2026-11-10）

```text
任务：T007 — 多客户端协同分发与基础策略
基线分支与起点 SHA：feat/T007-distribution-strategies @ dbe69d116fb6ecf15b0b7d1c9dae842d3b2c5a55（PR #6 合并后的 main）
实际代码变化：
  新增 src/lan_distribution_lab/distribution.py：控制器复用 T006 ResumableDownloader，为每个逻辑客户端使用独立凭据与独立输出路径；sequential 为单线程逐个执行（--stop-on-error 时未开始者标记 cancelled），parallel 用有界 ThreadPoolExecutor（客户端数 ≤ 8）让多个阻塞 HTTP 下载真实重叠；记录每客户端 task_id、状态、UTC 起止时间、time.monotonic() 耗时、下载字节、resumed_bytes 与安全错误类型，并汇总运行级 file_id/大小/SHA-256/成功失败取消数与总耗时。
  新增 src/lan_distribution_lab/distribution_store.py：独立 SQLite distribution_runs/distribution_clients 运行记录，不改变服务端 API 与 transfer_records。
  client.py 增加 AuthenticationError、ServerBusyError（均为 DownloadError 子类）与公开 metadata() 预检；未改变下载协议与重定向安全策略。
  CLI 新增 `python -m lan_distribution_lab distribute`：--server/--file-id/--strategy/--clients/--output-dir/--output-name/--pair|--tokens-stdin/--timeout/--retries/--stop-on-error/--results/--records；凭据仅内存，输出 client-<n>/ 子目录，JSON 原子写入，SQLite 追加记录；输出目录与文件名做路径安全检查。
测试：PASS — 新增 tests/test_distribution.py 16 passed：真实 uvicorn 服务器上 3 个独立会话顺序下载且 SHA-256 正确、transfer_records 的 session_id 互不相同；parallel 用服务端同时在途请求计数证明确实重叠；超时/认证失败/中断互不影响；服务端槽位上限返回 service_unavailable；顺序 stop-on-error 取消剩余；Range/If-Range 续传与最终 SHA-256；JSON 与 SQLite 不含 token 或私人绝对路径；重复运行、空文件、同名文件与已有目标不覆盖；CLI 端到端与参数校验。
完整回归：`python -m compileall -q src tests` 通过；`python -m pytest`（Python 3.13.7：104 passed, 1 skipped, 1 warning in 23.58s）；`python -m pip check`、`npm --prefix web run test`（2 passed）、`npm --prefix web run build`、`git diff --check` 见下方 CI/验收更新。
真实设备/网络：NOT_RUN — 控制器运行在一台电脑上，逻辑客户端使用本机独立会话；未做手机、平板或真实 Wi-Fi 的多设备分发，也不把本机线程当作真实远程设备。
已知限制：未实现 T008 动态调度/优先级/速率控制；并发上限 8；服务端槽位不足时客户端直接失败（不做无限重试）；HTTP 无 TLS；Windows ACL 与断电一致性未实测。
审核补修（2026-11-10，待 PR#7 复审）：(1) 记录路径安全——下载启动前规范化校验 --results/--records 与各客户端目标、.part、.part.json，结果与记录不得同路径，冲突提前失败且不启动下载、不写结果、不改变已有文件；write_result_json 支持受保护路径并在原子替换前拒绝覆盖；SQLite 只追加不覆盖。(2) 独立凭据——同一分发内重复 Bearer Token 提前拒绝且不打印 token；--pair 保持每客户端独立配对码。新增 10 项回归测试（16 → 26），完整 pytest 为 114 passed, 1 skipped。
本地提交 SHA：940d7c66cabec7d34dc89618177e91f5143c1fe1（fix：路径与凭据）、6eb23c95aed4f3135931111606ca91aa75c5ef46（docs：README/TASKS/DECISIONS/STATUS）。
当前分支、工作区状态、PR：feat/T007-distribution-strategies；PR #7（https://github.com/cppppercy/lan-distribution-lab/pull/7）未合并，HEAD 为 6eb23c95aed4f3135931111606ca91aa75c5ef46（以远端为准）。
本地提交 SHA：86e1cb80e4ceb17e357ad8392e7d1436ba6eda23（feat：distribution 与 tests）、0969200528c75fd18418c5551b6ae3d53bd3ec98（docs：README/ARCHITECTURE/TASKS/DECISIONS/STATUS）。
GitHub Actions：PASS（T007 主体）— push [run 42763362857](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42763362857/job/129654024200)（1 分 25 秒）、PR [run 42763385090](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42763385090/job/129654093609)（2 分 16 秒）。PASS（审核补修 940d7c6/6eb23c9）— 两次工作流均在 Windows / Python 3.12 成功执行 npm ci、前端测试/构建与 pytest：push [run 42766806859](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42766806859/job/129665095996)（1 分 46 秒）、PR [run 42766812838](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42766812838/job/129665114472)（1 分 32 秒）。本记录自身的文档提交会再次触发工作流，最终状态以 PR #7 Checks 为准；不自动合并。
合并状态（2026-11-10）：PR #7 已由用户合并至 main，合并提交 155462e5324f915645f490b02847fa3a5e46addf；T007 状态更新为 DONE。
```

## T006 验证记录（2026-11-09）

```text
任务：T006 — Python CLI 可靠断点续传
实际代码变化：新增标准库 urllib 下载客户端和 `python -m lan_distribution_lab download`。默认无回显输入 Bearer token；`--pair` 无回显输入一次性码，在内存中兑换 token；没有 --token 参数，也不把 token 写入 URL、日志、.part 或 .part.json。客户端以邻接 .part/.part.json 记录强 ETag、SHA-256、大小、server、file_id 和安全已落盘偏移；续传只请求 Range + If-Range，并严格检查 200/206/416、Content-Range、Content-Length、ETag 和接收字节。每块 fsync 后原子状态更新；最终 SHA-256 正确才用不覆盖已有文件的硬链接发布目标。200/416、版本或本地状态冲突会隔离旧工件，错误 206/hash 不发布。
Codex 本地验证：PASS（T006 专用）— `python -m pytest tests/test_resumable_client.py -q`：9 passed in 0.14s，使用独立临时目录和可控本地 transport；覆盖完整下载/哈希、流截断恢复及仅缺失范围、200/416 安全重启、错误 206、损坏 partial、空文件、超时、ENOSPC、重复运行、token 不落盘和 CLI 配对兑换。完整回归：`python -m compileall -q src tests` 通过；`python -m pytest`（Python 3.13.7：76 passed, 1 skipped, 1 warning in 3.28s）；`python -m pip check` 通过；`npm --prefix web run test`（2 passed）和 `npm --prefix web run build` 均通过。GitHub Actions：PASS — [分支推送](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42586049773/job/129056020090) 与 [PR #6](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42586081906/job/129056122850) 均在 Windows / Python 3.12 成功执行 pytest、前端测试与生产构建（分别 1 分 4 秒、1 分 15 秒）。
真实设备/网络：NOT_RUN — 未在真实 Windows LAN、手机、平板或真实 Wi-Fi/热点上验证 CLI 中断恢复；这不由本机模拟替代。
已知限制：HTTP 无 TLS；同网段窃听仍可取得 CLI token/内容。Windows NTFS 通常支持最终硬链接发布，但 Windows ACL、断电一致性、FAT/网络盘和 reparse-point 的实际行为仍待补验。上传续传、多范围、浏览器后台续传、UDP、P2P 和调度未实现。
```

## T004 验证记录（2026-11-09）

```text
时间：2026-11-09
任务：T004 — 局域网 Web 界面与多终端互通
实际代码变化：新增 Vue 3 + TypeScript + Vite 的响应式单页界面；同源 Cookie 配对、文件列表、XHR 真实上传进度、原生浏览器下载发起、错误/加载/空状态与会话退出。FastAPI 仅在 web/dist 存在时于根路径提供构建后的静态资源，API 路由保持优先。serve 增加显式 --host，仅接受回环或私有 IPv4、拒绝 0.0.0.0/公网地址；默认 Origin 自动与监听 URL 匹配。Windows `scripts/start-lan.ps1` 在用户显式运行后优先选择移动热点 `192.168.137.1`，否则只在地址无歧义时选择私有 IPv4；它构建 Web、创建一次性配对码并调用同一受限 serve 命令，不修改防火墙、NAT 或端口映射。CI 增加 npm ci、前端测试和生产构建。
Codex 本地验证：PASS — npm audit（0 vulnerabilities）；npm run test（2 passed）；npm run build（Vue 类型检查和 Vite 生产构建通过）；python -m compileall -q src tests；python -m pytest（56 passed, 1 skipped, 1 warning）；短暂运行 127.0.0.1:8044 后 GET / 返回 Vue 根节点且 GET /api/health 返回 ok。新增脚本以 `-PrintOnly` 实测识别当前 Windows 移动热点 `192.168.137.1`；以 8004 完整运行时实测完成构建、配对码创建、该地址监听及 GET /api/health=200，之后仅停止该临时 8004 进程。现有文件 API 测试覆盖配对、上传/下载和 SHA-256 一致性；新增 Python 测试覆盖静态托管不遮蔽 API、LAN host/Origin 及拒绝不安全监听地址。
GitHub Actions：PASS — PR #4 的功能提交 `9327cbf4a4d1851001a15f9f4c5048e250992787` 在 Windows / Python 3.12 两次触发均通过，分别用时 62 秒和 59 秒；工作流实际执行了 Node 24 的 npm ci、前端测试、Vite 构建及 Python pytest。
真实设备验证：手机热点场景已验证（用户提供）— 2026-11-09，Windows 移动热点，服务 `192.168.137.1:8003`；手机成功访问 Vue 页面，Windows 电脑与手机分别使用独立 `read_write` 配对码，并确认双向文件收发成功。校园 Wi-Fi 曾超时，原因未确认。平板、文件大小、设备/浏览器信息、SHA-256 对照和断线实验：NOT_RUN。
已知限制：HTTP 无 TLS；浏览器下载只能报告“已发起”，不能证明文件已经在远端保存；会话刷新后服务器无读取 scope 的接口，页面显示“已授权”并让服务器继续强制权限；Range、续传、UDP、P2P 和调度不在本任务范围。
```

## T005 验证记录（2026-11-09）

```text
任务：T005 — HTTP Range 与文件版本保护
实际代码变化：下载端点保留无 Range 的完整 200，并实现单一 bytes=start-end、start-、-suffixLength 的 206 流；响应带 Accept-Ranges、Content-Range、范围 Content-Length 和强 ETag。多范围或非法语法返回 400 invalid_range；不可满足范围返回 416 和 bytes */total。If-Range 仅精确强 ETag 匹配时返回 206；弱/日期/不匹配值回退完整 200。流通过 seek 和剩余字节计数发送，范围任务的 size_bytes/bytes_transferred 是本次响应范围，不是完整对象。
Codex 本地验证：PASS — python -m compileall -q src tests；python -m pytest（Python 3.13.7：67 passed, 1 skipped, 1 warning）；python -m pip check（No broken requirements found）；npm --prefix web run test（2 passed）；npm --prefix web run build（通过）。新增范围测试将多个 206 数据段顺序拼接后验证 SHA-256 与原对象一致，并覆盖空文件、非法/多范围、416、If-Range、未授权、流式 read 上界与下载槽位释放。
GitHub Actions：PASS — [分支推送](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42582870820/job/129045576098) 与 [PR #5](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42582914355/job/129045714390) 均在 Windows / Python 3.12 成功执行 pytest、前端测试与生产构建（分别 1 分 19 秒、1 分 15 秒）。
非目标/限制：未实现多范围 MIME、Python CLI 断点续传、上传续传、UDP、P2P 或调度；Range 的 completed 仅说明服务器读完并发送了所选响应范围，不能证明远端客户端保存完成。Windows 测试环境仍不允许创建符号链接，相关 reparse-point 测试跳过。
```

## T003-B 验证记录（2026-11-09）

```text
时间：2026-11-09
任务：T003-B — 受控文件上传、下载、校验及任务记录
实际代码变化：有界 multipart 流解析；staging→服务器 UUID objects 发布；SHA-256、SQLite file_metadata/transfer_records、文件列表/元数据/完整下载、受控任务查询、请求/文件/配额/磁盘/并发限制与清理。
测试：PASS — `python -m compileall -q src tests`；`python -m pytest`（Python 3.13.7：37 passed, 1 skipped, 1 warning in 2.37s）；`python -m pip check`（No broken requirements found）。pytest 覆盖文件上传下载、完整性、0 B、Unicode/重名、权限/撤销、路径/展示名、配额、ENOSPC 注入、上传清理、任务隐私、Cookie Origin、槽位和流式超限读取。
真实设备证据：NOT_RUN；未测试手机、平板、真实 Wi-Fi、热点或公网。
已知限制：不支持 Range、If-Range、上传幂等键或续传；HTTP 无 TLS；Windows 受限测试环境无法创建符号链接，reparse-point 竞态未验证。
```

## T003 最终验收补修记录（2026-11-09）

```text
时间：2026-11-09
任务：PR #3 的 T003 最终验收补修
实际代码变化：上传路由将 ClientDisconnect、取消、解析/API/存储异常统一收敛到 finally 清理；staging 文件、传输失败状态和上传槽位均由幂等清理路径处理。multipart 文件名按 UTF-8（含 RFC 5987 filename*）解析；只有后接 CRLF 或 -- 的序列才是边界，解析器单次内部切片最多 64 KiB。文件发布时在 SQLite BEGIN IMMEDIATE 事务内完成配额检查、对象发布和元数据写入，避免并发发布突破总配额。Git 忽略规则新增 SQLite 旁文件、server-secret.bin、objects/ 和 staging/。
Codex 本地验证：PASS — `python -m compileall -q src tests`；`python -m pytest`（Python 3.13.7，48 passed, 1 skipped, 1 warning in 3.04s）；`python -m pip check`（No broken requirements found）；`git diff --check`；`git check-ignore` 已确认本地 SQLite、密钥和 objects/staging 均被忽略。新增测试包含 ASGI http.disconnect 的真实请求流、UTF-8/ASCII/空格文件名、非法 UTF-8、边界伪装及跨分块、64 KiB 有界转发、并发配额发布。
GitHub Actions：PASS — PR #3 的最终补修代码提交 `40945a93a721f47162f9a2033b6994c3c3ac913e` 已在 Windows / Python 3.12 通过两次触发的 pytest 工作流（分别 42 秒、47 秒）。本记录的后续文档提交不改变该代码验证结论，PR 当前提交状态仍应以 Checks 为准。
真实设备验证：NOT_RUN — 未在手机、平板、真实 Wi-Fi/热点或跨设备 LAN 上执行上传下载/断线实验。
仍未验证：受限 Windows 环境不能创建符号链接，reparse-point 测试仍跳过；HTTP 无 TLS；Range/If-Range、断点续传、前端、UDP 和分发调度未实现。
```

## T003-A 验证记录（2026-11-09）

```text
时间：2026-11-09
任务：T003-A — 安全配对、会话授权与存储基础
基线 SHA：b65f2a8b673d92ecb81470e28e826e1adb8f185d（PR #2 合并后的 main）
实际代码变化：本机 argparse 配对命令；FastAPI 配对/撤销端点；统一安全错误；HMAC 摘要的六码/令牌；SQLite 会话、限速和 file_metadata 基础表；受控 objects/staging 目录；可复用 read/read_write 依赖。
测试命令和结果：PASS — .\.venv\Scripts\python.exe -m pytest（Python 3.13.7；20 passed, 1 warning in 1.30s）；并短暂以独立临时目录运行 `pair create` 与 `serve --port 8033`，对 `http://127.0.0.1:8033/api/health` 的实际请求通过后已停止进程。pytest 覆盖健康回归、配对有效性/过期/重复/并发、限速、Cookie/Bearer、scope、撤销、Origin、日志/数据库不含明文秘密、重启持久化和目录隔离。
真实设备证据：NOT_RUN；未测试手机、平板、真实 Wi-Fi、热点或公开 LAN。
已知失败/未运行项：warning 为 Starlette TestClient 使用 AnyIO 已弃用类型别名。未做 Windows ACL/reparse-point 行为验证；HTTP 不提供 TLS 机密性；文件上传/下载、配额、SHA-256 文件校验、Range、任务记录均未实现。
GitHub Actions：PASS — [分支推送](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42543573951/job/128923646789)（39 秒）与 [PR #3](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42543609733/job/128923758919)（40 秒）均在 Windows / Python 3.12 成功运行 pytest。
当前分支、工作区状态、PR：`feat/T003A-auth-storage`；[PR #3](https://github.com/cppppercy/lan-distribution-lab/pull/3)，未合并。
下一任务：T003-B，前提为 T003-A 审查；其实现不得绕过现有会话、scope、目录隔离和默认 localhost 策略。
```

## T002 验证记录（2026-11-08）

```text
时间：2026-11-08
任务：T002 — 文件 API 契约、安全威胁模型与测试方案设计
基线 SHA：e8a00367489f386ef21685747e4661339e203413（PR #1 合并后的 main）
实际变化：新增 API_CONTRACT、THREAT_MODEL、TEST_PLAN；更新 ARCHITECTURE、DECISIONS、TASKS、STATUS。没有修改运行时代码、依赖、数据库或防火墙。
静态检查：PASS — 本地 Markdown 链接均存在；必需 API 路径齐全；未发现把 T003/T005/T006 功能写为已实现的标记。
测试命令和结果：PASS — .\.venv\Scripts\python.exe -m pytest（Python 3.13.7；2 passed, 1 warning in 0.39s）。
真实设备证据：NOT_RUN；未测试手机、平板、真实 Wi-Fi、热点、文件传输、Range 或 CLI 续传。
已知失败/未运行项：pytest 的 1 条警告来自 Starlette TestClient 使用 AnyIO 已弃用类型别名；文件/授权测试仅为设计，尚无实现可运行。
GitHub Actions：PASS — [PR #2](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42463779994/job/128678507058) 在 Windows / Python 3.12 成功执行现有 pytest（30 秒）。
当前分支、工作区状态、PR：`docs/T002-api-security-design`；PR #2，后续提交与状态以 Git 记录为准。
下一任务：T003，前提是审查并接受本任务的 API 与安全设计。
```

## T001 验证记录（2026-11-08）

```text
时间：2026-11-08
任务：T001 — LAN Distribution Lab 工程初始化与最小服务基线
基线 SHA：d6420eb3ba2e383a236078de1335bf2c1e256db6
实际代码变化：FastAPI 健康端点、127.0.0.1:8000 启动入口、pytest、精确依赖与传递依赖约束、GitHub Actions、架构和实验文档。
测试命令和结果：
  PASS — .\.venv\Scripts\python.exe -m pip check（No broken requirements found）
  PASS — .\.venv\Scripts\python.exe -m pytest（Python 3.13.7；2 passed, 1 warning in 0.42s）
  PASS — 短暂运行 python -m lan_distribution_lab 后，Invoke-RestMethod http://127.0.0.1:8000/api/health 返回 status=ok、service=lan-distribution-lab；测试后已停止进程。
真实设备证据：NOT_RUN；未测试手机、平板、真实 Wi-Fi 或热点。
已知失败/未运行项：pytest 的 1 条警告来自 Starlette TestClient 使用 AnyIO 已弃用类型别名；不影响本次 2 项断言。真实设备、真实 LAN 和性能实验仍为 NOT_RUN。
GitHub Actions：PASS — [分支推送](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42461658261/job/128672044358)（25 秒）和 [PR #1](https://github.com/cppppercy/lan-distribution-lab/actions/runs/42461686926/job/128672133644)（27 秒）均以 Python 3.12 运行 pytest 成功。
当前分支、工作区状态、PR：`feat/T001-bootstrap`；PR #1，后续提交与状态以 Git 记录为准。
下一任务：T002（文件 API 设计与威胁模型），前提为 T001 分支审查完成。
```

## 已知风险与待定问题
- 手机、平板的系统及浏览器版本尚未记录，不能承诺完整移动下载续传。
- 家庭 Wi-Fi/热点的无线设备互访、隔离与性能差异需 G1/G4 实测。
- Windows 防火墙规则不得由 Agent 未经许可改动。
- 传输策略不预设性能提升，需后续对照实验。

## 更新格式（后续每次执行必须维护）
```text
时间：
任务：
基线 SHA / 完成 SHA：
实际代码变化：
测试命令和结果：
真实设备证据：
已知失败/未运行项：
当前分支、工作区状态、PR：
下一任务：
```
