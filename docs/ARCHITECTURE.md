# 当前架构（T001）

**状态**：T001 最小服务基线。仅健康检查已实现；文件传输、存储、认证、数据库、设备发现、CLI 续传、前端 UI 与调度均未实现。

## 运行组成与边界

```mermaid
flowchart LR
    operator["Windows 操作者"] --> command["python -m lan_distribution_lab"]
    command --> server["Uvicorn + FastAPI\n127.0.0.1:8000"]
    client["本机 HTTP 客户端"] -->|"GET /api/health"| server
    server --> response["JSON 健康响应"]
```

服务入口为 `src/lan_distribution_lab/__main__.py`，固定使用 `127.0.0.1:8000`。它不会默认监听局域网接口、开放公网端口或修改 Windows 防火墙。显式 LAN 模式是后续任务的受控设计项，不属于 T001。

## 模块边界

| 路径 | 当前职责 | 状态 |
| --- | --- | --- |
| `src/lan_distribution_lab/app.py` | FastAPI 应用与 HTTP 路由 | 已实现 |
| `src/lan_distribution_lab/__main__.py` | 本地安全默认值的 Uvicorn 启动入口 | 已实现 |
| `tests/test_health.py` | 健康接口的本地 HTTP 集成测试 | 已实现 |
| 传输、存储、认证、调度、发现、Web UI | 后续按任务拆分 | 未实现 |

## 已实现接口契约

### `GET /api/health`

- 用途：公开、无敏感信息的进程/HTTP 服务存活检查。
- 成功响应：`200 OK`，`application/json`，响应体严格为：

```json
{"status":"ok","service":"lan-distribution-lab"}
```

- 错误边界：当前路由仅接受 GET；例如 `POST /api/health` 由 FastAPI 返回 `405 Method Not Allowed`。不存在的路径由框架返回 `404`。
- 不代表：文件共享可用、授权有效、数据库可用或局域网可达；这些能力在 T001 尚未实现，不能从健康结果推断。

## 启动与依赖

项目使用 `pyproject.toml`：直接运行/开发依赖均精确固定；`requirements.lock` 约束已解析的传递依赖。Python 支持范围为 3.12 至 3.13。Windows PowerShell 的实际命令见 [README](../README.md)。CI 使用 Python 3.12，在不需要密钥、外部业务服务或数据库的条件下运行同一 pytest 命令。T001 本机以 Python 3.13 实测 `pytest` 为 2 通过（含 1 条上游依赖弃用警告），并以实际本机 socket 请求验证了健康响应。
