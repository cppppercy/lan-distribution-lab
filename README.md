# LAN Distribution Lab

面向局域网的可靠文件传输与多终端协同分发系统（课程项目）。

> 项目状态：T001 最小工程基线已在本机验证。当前只实现本机健康接口；尚未实现文件传输、续传、设备发现、调度或页面 UI；不得将规划描述为已交付。

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

## 当前可运行基线（T001）

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

### 运行自动化测试

```powershell
python -m pytest
```

测试通过仅说明本地 HTTP 健康接口符合当前契约；它不等同于手机、平板、真实 Wi-Fi 或文件传输验证。详见 [当前架构](docs/ARCHITECTURE.md) 与 [实验记录](docs/EXPERIMENTS.md)。
