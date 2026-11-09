# 当前项目状态

**更新日期**：2026-11-09
**整体状态**：T003-A 的本机配对、会话授权和存储基础已实现并在本地自动化测试中通过；T003-B 的文件业务尚未开始，T003 整体仍为 IN_PROGRESS。

## 事实（已确认）
- GitHub 仓库：`cppppercy/lan-distribution-lab`，默认 main；最初为空。
- 用户环境：Windows、32 GB 内存，手机与平板两台移动设备，家庭 Wi-Fi 或手机热点。
- 用户确认局域网基本连接没有问题；项目专用服务的连通性/性能尚无运行记录。
- 编码协作：Codex 优先、DSH 备用，ChatGPT 制定需求和审核。
- 已完成文档：README、REQUIREMENTS、AGENTS、TASKS、DECISIONS、STATUS（以仓库实际提交为准）。
- 项目代码：最小 Python 包、仅本机监听的 Uvicorn 启动入口、`GET /api/health`、本机配对 CLI、`POST /api/pairings`、会话撤销、SQLite 状态与 pytest：**IMPLEMENTED**。
- 文件传输、设备发现、续传、多客户端分发：**NOT_IMPLEMENTED**。
- T002 设计文档：文件 API 契约、配对/会话威胁模型、存储/版本原则、测试矩阵：**COMPLETED（设计，不是业务实现）**。
- GitHub Actions 工作流：**IMPLEMENTED 且 PASS**。T001 分支推送与 PR #1 均在 Python 3.12 成功执行 pytest（25 秒、27 秒）。
- 真实手机/平板测量、断线实验、性能结果：**NOT_RUN**。

## 当前任务
- **T001 — DONE**：最小工程初始化、健康接口、自动化测试、CI 配置和启动说明已按任务边界完成。完整证据见下方记录与 `docs/TASKS.md`。
- **T002 — DONE**：API 契约、安全威胁模型和测试方案已完成，且未越界实现文件业务。详见 `docs/API_CONTRACT.md`、`docs/THREAT_MODEL.md`、`docs/TEST_PLAN.md`。
- **T003-A — DONE**：配对码、会话、撤销、scope 与存储基础实现完成；T003-B 前不提供文件接口。
- **T003 — IN_PROGRESS**：必须在 T003-A 与 T003-B 都完成并经审核后才能标记 DONE。
- 下一关卡：T003-B 可在审查 T003-A 后开始；G1 的文件收发、真实设备和真实 LAN 证据仍未获得。

## T003-A 验证记录（2026-11-09）

```text
时间：2026-11-09
任务：T003-A — 安全配对、会话授权与存储基础
基线 SHA：b65f2a8b673d92ecb81470e28e826e1adb8f185d（PR #2 合并后的 main）
实际代码变化：本机 argparse 配对命令；FastAPI 配对/撤销端点；统一安全错误；HMAC 摘要的六码/令牌；SQLite 会话、限速和 file_metadata 基础表；受控 objects/staging 目录；可复用 read/read_write 依赖。
测试命令和结果：PASS — .\.venv\Scripts\python.exe -m pytest（Python 3.13.7；20 passed, 1 warning in 1.30s）；并短暂以独立临时目录运行 `pair create` 与 `serve --port 8033`，对 `http://127.0.0.1:8033/api/health` 的实际请求通过后已停止进程。pytest 覆盖健康回归、配对有效性/过期/重复/并发、限速、Cookie/Bearer、scope、撤销、Origin、日志/数据库不含明文秘密、重启持久化和目录隔离。
真实设备证据：NOT_RUN；未测试手机、平板、真实 Wi-Fi、热点或公开 LAN。
已知失败/未运行项：warning 为 Starlette TestClient 使用 AnyIO 已弃用类型别名。未做 Windows ACL/reparse-point 行为验证；HTTP 不提供 TLS 机密性；文件上传/下载、配额、SHA-256 文件校验、Range、任务记录均未实现。
GitHub Actions：NOT_RUN；本记录生成时尚未推送 T003-A 分支，推送后应查看 PR 工作流结果。
当前分支、工作区状态、PR：`feat/T003A-auth-storage`；提交与 PR 在本任务收尾后以 GitHub 记录为准。
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
