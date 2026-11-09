# LAN Distribution Lab

面向局域网的可靠文件传输与多终端协同分发系统（课程项目）。

> 项目状态：T003-A 的本机配对、会话授权与存储基础已在自动化测试中验证；尚未实现文件上传/下载、续传、设备发现、调度或页面 UI。默认服务仍只监听本机。

## 项目目标

在 Windows 主机运行局域网文件服务，让手机、平板通过浏览器访问、上传及下载文件；使用 Python 专用客户端验证断点续传与并发分发，并通过可重复的实验比较分发策略。

- 课程主线：HTTP/TCP、客户端—服务器通信、UDP 设备发现、并发传输、错误恢复与网络测量。
- 主要环境：Windows 32GB；真实客户端为手机、平板；家庭 Wi-Fi 或手机热点；不要求云服务器。
- 开发协作：用户决策，ChatGPT 负责需求/审核，Codex 主编码，DeepSeek Harness（DSH）可接力。
- 工作准则：小步实现，测试证据优先，不以界面丰富代替网络技术深度。

## 规范入口

- [需求规格说明书](docs/REQUIREMENTS.md)
- [编码 Agent 共同规则](AGENTS.md)
- [任务清单和交接协议](docs/TASKS.md)
- [当前状态](docs/STATUS.md)
- [技术决策记录](docs/DECISIONS.md)

## 重要限制

本项目首先用于可信局域网的实验与课程学习。不得默认开放公网；不得把本机模拟并发测试宣称为多台无线设备实测。原生断点续传首先由 Python 客户端保证，移动浏览器的能力需按实际平台验证。

代码、密钥、真实成果以当前仓库与测试记录为准。本文不声称任何尚未执行的测试通过。

## 当前可运行基线（T001 + T003-A）

### 前置条件

- Windows PowerShell 5.1+。
- Python 3.12 或 3.13。可用 `py -0p` 查看已安装解释器；T001 的本地验证使用 Python 3.13，CI 固定使用 Python 3.12。

### 安装

在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -c requirements.lock -e ".[dev]"
```

如果系统未安装 Python 3.12，可将第一条中的 `-3.12` 替换为已安装的 Python 3.13。直接依赖在 `pyproject.toml` 精确固定，传递依赖由 `requirements.lock` 约束；不要把 `.venv`、`.env` 或密钥提交到仓库。

### 启动本机服务

```powershell
python -m lan_distribution_lab
```

该入口固定监听 `http://127.0.0.1:8000`，不会默认开放局域网或修改 Windows 防火墙。保持该终端运行后，在另一 PowerShell 窗口验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

预期输出：

```text
status service
------ -------
ok     lan-distribution-lab
```

### 本机生成配对码（T003-A）

配对码只能由 Windows 主机上的命令行生成，**没有**对应的 LAN 管理 HTTP 接口。为避免“生成码的状态库”和服务端不一致，`pair create` 与 `serve` 必须使用同一组绝对 `--data-dir` 和 `--managed-root`；两个目录必须分离。

```powershell
$dataDir = "C:\LANDistributionLab\state"
$managedRoot = "C:\LANDistributionLab\managed"

python -m lan_distribution_lab pair create --scope read --data-dir $dataDir --managed-root $managedRoot
python -m lan_distribution_lab serve --data-dir $dataDir --managed-root $managedRoot
```

`--scope` 可取 `read`（默认）或 `read_write`。命令显示一个由密码学安全随机数生成的六位数字码及 UTC 过期时间；它只能使用一次，默认十分钟过期。服务端使用同一 SQLite 状态库，以原子事务兑换码并创建最多八小时的会话。

`POST /api/pairings` 已实现：浏览器必须提供与配置精确匹配的 `Origin`，成功后得到 `HttpOnly; SameSite=Strict; Path=/api` Cookie；CLI 才会在 JSON 响应体中一次性得到 Bearer token。服务端只保存 HMAC 摘要，不在 URL 或应用错误中输出令牌。`DELETE /api/sessions/current` 可撤销当前会话；Cookie 会话的撤销同样检查 `Origin`。

当前 HTTP 未启用 TLS，Cookie 的 `Secure` 属性必须为假才能在 HTTP 工作。这些控制能拒绝未持有凭据的请求、缩短凭据有效期并抵御常见 CSRF；**不能**防止同一局域网的窃听或中间人。不要把配对码或 CLI token 发送给不可信对象，也不要将本机服务暴露到公网。

`managed_root/objects` 和 `managed_root/staging` 已创建为未来受控文件对象目录；T003-A 没有文件业务 API，也不会读写用户文件。

### 运行自动化测试

```powershell
python -m pytest
```

当前自动化测试覆盖健康接口、配对码一次性/过期/并发兑换、限速、Cookie/Bearer、权限范围、撤销、Origin、摘要存储与重启持久化。它不等同于手机、平板、真实 Wi-Fi、Windows ACL/reparse point 或文件传输验证。详见 [当前架构](docs/ARCHITECTURE.md)、[威胁模型](docs/THREAT_MODEL.md) 与 [实验记录](docs/EXPERIMENTS.md)。
