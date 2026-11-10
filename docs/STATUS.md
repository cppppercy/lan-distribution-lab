# 当前项目状态

**更新日期**：2026-11-10
**整体状态**：T003 和 T005 已合并至 main；T004 已获得手机热点基础收发的用户验证但 G1 人工项目未补齐；T006 的 Python CLI 安全续传已完成审核补修（重定向凭据、完整 `.part` 复用、续传统计），PR #6 等待最终 CI 与复审。

## 事实（已确认）
- GitHub 仓库：`cppppercy/lan-distribution-lab`，默认 main；最初为空。
- 用户环境：Windows、32 GB 内存，手机与平板两台移动设备，家庭 Wi-Fi 或手机热点。
- 用户确认局域网基本连接没有问题；项目专用服务的连通性/性能尚无运行记录。
- 编码协作：Codex 优先、DSH 备用，ChatGPT 制定需求和审核。
- 已完成文档：README、REQUIREMENTS、AGENTS、TASKS、DECISIONS、STATUS（以仓库实际提交为准）。
- 项目代码：最小 Python 包、仅本机监听的 Uvicorn 启动入口、`GET /api/health`、本机配对 CLI、`POST /api/pairings`、会话撤销、SQLite 状态与 pytest：**IMPLEMENTED**。
- 文件上传/下载核心、SHA-256、元数据和传输摘要：**IMPLEMENTED（T003-B）**；HTTP Range 与 Python CLI 下载续传：**IMPLEMENTED（T005/T006，T006 审核补修后待复审）**；设备发现、多客户端分发：**NOT_IMPLEMENTED**。
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
- **T006 — IN_PROGRESS**：Python CLI `.part`/无秘密状态、Range/If-Range 恢复、强响应校验、SHA-256 发布和自动化故障测试已实现；2026-11-10 完成审核补修（拒绝重定向转发凭据、完整 `.part` 免重复下载、`resumed_bytes` 统计归零），PR #6 未合并，等待 CI 与复审。
- 下一关卡：T006 审查通过后可进入 T007；T004 的剩余真实设备项目仍须如实补验。

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
