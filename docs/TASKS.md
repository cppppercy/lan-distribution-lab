# 开发任务与验收路线图

**基线版本**：1.0（2026-11-08）  
**状态取值**：`TODO` / `IN_PROGRESS` / `BLOCKED` / `DONE`。没有实测证据不得标记 `DONE`。  
**任务契约**：每项包含目标、非目标、允许修改的范围、依赖、验收证据和交接记录。

## 当前任务表

| ID | 状态 | 目标 | 关卡 | 依赖 |
| --- | --- | --- | --- | --- |
| T001 | DONE | 初始化工程和最小 HTTP 健康接口、自动化测试、CI、启动说明 | G0 | 规范已发布 |
| T002 | DONE | 文件 API 设计与威胁模型；接口契约、权限与路径安全测试设计 | G1 设计前置 | T001 |
| T003 | DONE | 后端受控共享文件、列表、下载、上传与校验；T003-A/B 已合并并通过最终补修审核 | G1 | T002 |
| T003-A | DONE | 本机配对码、会话授权、SQLite 状态和受控存储目录基础 | G1-A | T002 |
| T003-B | DONE | 受控文件上传/下载、校验、元数据与传输记录 | G1-B | T003-A |
| T004 | IN_PROGRESS | Vue 响应式界面、同源静态托管、显式 LAN 启动和 Windows 自动选址启动脚本已实现；手机热点基础互传已验证，G1 剩余人工项目待补验 | G1 | T003 |
| T005 | DONE | 单 HTTP Range、If-Range 版本保护、范围任务语义与边界测试已合并 | G2 | T003 |
| T006 | IN_PROGRESS | Python CLI 断点续传、失败恢复、完整性验证已实现；等待审查/PR 验证 | G2 | T005 |
| T007 | TODO | 多会话任务记录、顺序与普通并发策略 | G3 | T006 |
| T008 | TODO | 受控并发策略与调度实验入口 | G3 | T007 |
| T009 | TODO | UDP 桌面发现、连接回退及设备授权体验 | G3 | G1 稳定后 |
| T010 | TODO | 自动化实验驱动、数据记录、真实设备测试 | G4 | T008 |
| T011 | TODO | 抓包与网络理论分析、性能图表和局限性评估 | G4 | T010 |
| T012 | TODO | 稳定版本打包、完整使用说明、课程报告素材 | G5 | G4 |

### T001（第一条 Codex 指令，严格限定）

**目标**：让当前仅含规范文档的仓库具备可重复启动、可测试、可继续开发的最小工程，不实现文件传输。

**实施范围**
- 读取所有已存在的项目规范并检查 Git 状态、分支与 HEAD。
- 建立 Python 后端最小包、配置入口和 `GET /api/health`，默认监听 localhost。
- 为健康接口添加 pytest（单测/HTTP 集成），确保本机可执行、无外网依赖。
- 建立 Python 依赖/工具配置、`.gitignore` 与 GitHub Actions 的 Python 测试任务；使用固定 Python 版本与可重复的依赖管理。
- 创建 `docs/ARCHITECTURE.md`（真实基线而非假想架构）与 `docs/EXPERIMENTS.md`（模板，未运行实验要标明 NOT_RUN）。
- 更新 `README.md` 的 Windows PowerShell 真实启动命令、`docs/STATUS.md` 与本任务状态。
- 不预建复杂 Vue 页面；前端安装与构建留给后续任务，除非证据表明 T001 必需。

**非目标**：文件上传/下载、访问码、Range/续传、SQLite 业务表、UDP 发现、并发策略、页面 UI、云部署、实际手机/平板测试。禁止将这些功能标记完成。

**验收条件**
1. 在干净的 Windows 环境中根据 README 安装依赖并启动，`GET /api/health` 应返回明确的 200/JSON 健康结果。
2. pytest 至少覆盖健康响应和一个合理错误边界；给出准确执行命令与输出。
3. GitHub Actions 可在无密钥、无外网业务请求、无数据库情况下执行相同测试；如无法等到远端 CI，标注 `NOT_RUN`。
4. README、ARCHITECTURE、STATUS、TASKS 与实际代码状态一致，安全默认监听 localhost。
5. 改动在专门分支、可审查；按用户在任务 prompt 中的授权推送分支并创建 PR，不自动合并。

**允许改动范围**：必要的项目配置、后端最小目录、测试、`.github/workflows`、`README.md`、`docs/ARCHITECTURE.md`、`docs/EXPERIMENTS.md`、`docs/STATUS.md`、`docs/TASKS.md`；本任务不应重写 `docs/REQUIREMENTS.md`、`AGENTS.md` 的约束。

**交接记录（2026-11-08）**：已在 `feat/T001-bootstrap` 实现 `GET /api/health`、本地入口、pytest、依赖锁约束和 Windows CI 工作流。Python 3.13 本机执行 `python -m pytest`：`2 passed, 1 warning`；短暂启动仅绑定 `127.0.0.1:8000` 的服务后，实际请求返回 `status=ok`、`service=lan-distribution-lab`。GitHub Actions 的分支推送和 PR #1 均在 Python 3.12 通过（见 `docs/STATUS.md`）；本次未做真实手机/平板、LAN、传输或性能实验。

### T002（文件 API 契约、安全威胁模型与测试方案设计）

**目标**：为 T003 受控文件收发提供可审查的 HTTP 契约、局域网安全边界、存储模型和分阶段测试方案；不实现文件业务。

**完成内容**：新增 `docs/API_CONTRACT.md`、`docs/THREAT_MODEL.md`、`docs/TEST_PLAN.md`；将架构、ADR、状态同步到“短时配对码 + 限时会话”“服务端 UUID + 不可变内容快照”设计。具体端点、错误格式、大小/配额、ETag/If-Range 关系及 T003--T006 测试路径均已定义。

**非目标**：未实现上传、下载、认证、SQLite、Range、CLI、Vue、UDP、调度、真实设备或性能实验；这些均不得标记完成。

**验收证据（2026-11-08）**：本地 Markdown 链接、必需 API 路径和未来功能状态检查通过；`python -m pytest` 在 Python 3.13.7 下为 `2 passed, 1 warning in 0.39s`。PR #2 的 GitHub Actions 亦在 Windows / Python 3.12 通过（30 秒）。警告来自上游 Starlette/AnyIO 弃用类型别名。没有文件传输或真实 LAN 测试结果。

**对 T003 的建议范围**：按契约实现配对/会话、`managed_root`、私有 staging、完整流式上传下载、SHA-256、任务摘要和对应 P0 自动化测试；Range/If-Range、CLI 续传及移动端真实互通分别留给 T005/T006/T004。

### T003-A（认证、会话及存储基础）

**完成内容（2026-11-09）**：实现本机 `pair create` 控制命令、六位一次性十分钟配对码、`POST /api/pairings`、浏览器 HttpOnly/SameSite Cookie、CLI Bearer token、`DELETE /api/sessions/current`、scope 依赖、SQLite 会话/限速状态与受控 `objects`/`staging` 目录基础。服务仍默认绑定 `127.0.0.1`，未创建任何文件业务 API。

**验收证据**：`.\.venv\Scripts\python.exe -m pytest` 于 Python 3.13.7 下通过 `20 passed, 1 warning in 1.30s`；PR #3 的 GitHub Actions 在 Windows / Python 3.12 也通过。测试使用独立临时目录，覆盖有效/重复/过期/并发兑换、失败锁定、Cookie/Bearer、权限范围、撤销、Origin、无明文 token、重启持久化与原健康接口。warning 为上游 Starlette TestClient 对 AnyIO 弃用类型别名的提示。

**未验证/非目标**：未做真实设备、真实 LAN、Windows ACL/reparse-point、TLS 或任何文件上传/下载测试；不将这些项目标为完成。

### T003-B（受控文件上传、下载、校验及任务记录）

**完成内容（2026-11-09）**：基于 T003-A 会话依赖，实现有界流式 multipart 上传、staging→UUID objects 发布、SHA-256、SQLite 文件元数据和传输摘要、列表/元数据/下载 API、配额/剩余磁盘/槽位限制、`ENOSPC` 清理与 Cookie Origin 检查。下载只提供完整 200 流。

**验收证据**：本地 pytest 覆盖正常上传下载/哈希、0 B、Unicode/重名、无授权/read 权限、撤销、恶意展示名、请求/文件/总配额、故障注入、上传清理、会话任务隔离、Origin、槽位和流式超限读取。最终命令和结果见 `docs/STATUS.md`。

**明确不做**：HTTP Range/If-Range（T005）、CLI 续传（T006）、上传幂等键、Vue、UDP、调度与真实设备性能实验。

**未验证**：真实设备/真实 LAN、Windows ACL、可创建符号链接/reparse-point 的 Windows 竞态、HTTPS；T003 整体须经过最终代码审核后才能标记 DONE。

### T004（局域网 Web 界面与多终端互通）

**完成内容（2026-11-09）**：新增 Vue 3 + TypeScript + Vite 单页界面，同源提供 Cookie 配对、文件列表、XHR 上传进度、浏览器原生下载发起、错误/加载/空状态和退出会话。FastAPI 在 `web/dist` 构建存在时提供静态页面；`serve --host <私有 IPv4>` 显式启用 LAN，拒绝 all-interface 和公网绑定，并以监听 URL 作为默认 Cookie Origin。`scripts/start-lan.ps1` 让 Windows 用户可一条命令完成私有地址选择、Web 构建、读写配对码创建和服务启动：优先移动热点 `192.168.137.1`，多网卡歧义时必须显式指定地址。CI 同时运行 npm 安装、前端测试/构建和 Python pytest。

**验收证据**：本地前端 `npm run test` 和 `npm run build`、完整 Python pytest、FastAPI 实际 localhost 静态页面/健康接口请求均已执行；Windows 自动启动脚本也已在当前移动热点地址完成地址选择、生产构建、配对码创建、监听和健康接口请求验证。具体结果记录在 `docs/STATUS.md`。前端不存储访问 token，不引入 CORS 绕过认证；上传/下载仍复用 T003 API 与安全限制。

**真实设备记录（2026-11-09，用户提供）**：Windows 移动热点、服务 `192.168.137.1:8003`；手机成功加载 Vue 页面，Windows 电脑与手机用独立 `read_write` 配对码完成配对，用户确认已双向文件收发成功。校园 Wi-Fi 曾超时，原因未确认。**仍待补验**：平板、文件大小与设备/浏览器信息、SHA-256 对照、断线实验；因此 T004 仍为 IN_PROGRESS，G1 不应宣告完整通过。

**非目标**：HTTP Range/续传、UDP、P2P、分发调度、HTTPS 和防火墙修改不在范围内。

### T005（HTTP Range 与文件版本保护）

**完成内容**：`GET /api/files/{file_id}/content` 在保留无 Range 完整 `200` 下载的基础上，实现 RFC 9110 单一 `bytes=start-end`、`start-` 与 `-suffixLength`。可满足请求为 `206`，带正确 `Content-Range`、`Content-Length`、`Accept-Ranges` 和强 ETag；多范围/非法语法为 `400`，不可满足范围为 `416` 且带 `Content-Range: bytes */total`。`If-Range` 只接受精确的强 ETag；不匹配时回退完整 `200`。范围流通过 seek 和剩余字节计数读取，不缓存整个对象；范围任务字节数仅记录该次响应范围。

**验收证据（本地）**：文件 API 自动化覆盖 200/206/416、精确/开放/后缀/截断范围、空文件、非法和多范围、未授权、If-Range、范围拼接 SHA-256、seek/read 上界、传输状态与下载槽位释放；完整套件/CI 结果见 `docs/STATUS.md`。

**非目标**：多范围 MIME 响应、上传续传、T006 Python CLI 断点续传、UDP、P2P、分发调度。

### T006（Python CLI 可靠断点续传）

**完成内容（待审查）**：新增 `python -m lan_distribution_lab download`。客户端以无回显 Bearer token 提示（或 `--pair` 无回显配对码即时兑换）认证；没有 token 命令行参数、URL token 或 token 持久化。它先读取受保护元数据，以相邻 `.part`/`.part.json` 保存强 ETag、SHA-256、大小、server、file_id 和已落盘字节；恢复时使用 `Range` + `If-Range`。每块流式写入并显示进度，数据 `fsync` 后原子写状态；最终流式 SHA-256 后以不覆盖已有目标的硬链接发布。

**恢复与失败语义**：只接受精确的 206、Content-Range、Content-Length 与 ETag。续传收到了完整 200、416 或本地/元数据版本不一致时，旧工件改名为随机 `.stale-*` 后最多重新开始一次；错误 206 和 hash 失败绝不追加或发布。网络错误默认有限重试两次；超时、磁盘写入失败、目标存在及不安全路径均返回失败而不覆盖用户目标。

**验收证据（本地）**：`tests/test_resumable_client.py` 覆盖完整 hash 下载、可控 HTTP 流截断后的缺失范围恢复、200/416 安全重启、错误 206 拒绝、损坏 partial、空文件、超时、ENOSPC 注入、重复执行、token 不写入状态以及配对换 token 的内存边界。完整套件、前端构建与远端 CI 结果记录在 `docs/STATUS.md`。

**非目标**：上传续传、多范围、浏览器后台续传、UDP、P2P、调度、TLS/公网部署和真实无线断线实验。

### T003 及后续任务共同模板

每次将任务细化后再发给执行 Agent，格式必须包括：

```text
任务编号与名称：
前提/依赖：
本次要解决的用户问题：
输入事实与当前基线 SHA：
必须实现的功能：
明确不做的功能：
技术边界与安全条件：
允许修改的文件：
验收用例（包括失败路径）：
执行命令与预期可观察结果：
提交、PR、状态记录要求：
```

## 里程碑冻结规则
- G0 在 T001 经验证后完成；用户先前确认的“LAN 基本可通信”只记作用户提供信息，不冒充程序级实测。
- G1 优先实现真实手机与平板的基础收发，二者均须有至少一项实际下载验证，至少一项移动端上传验证。
- G2 以 Python CLI 提供确定性断点续传承诺；浏览器平台差异另列测试。
- G3 的“协同”是中心服务器调度，不提前声称 P2P。
- G4 先定义实验变量和方法，再采集真实数据，关键对照至少重复三轮。
- G5 的报告必须区分课程老师明确要求、自主设计标准和实测事实。

## 变更管理
任何扩大范围、引入付费依赖/服务器、改变默认安全策略或放弃关键验收项，均需先写 `docs/DECISIONS.md` 并经用户批准。完成每项任务后更新状态、测试与下一步。
