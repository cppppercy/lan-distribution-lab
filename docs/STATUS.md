# 当前项目状态

**更新日期**：2026-11-08  
**整体状态**：T001 最小工程已在本机完成并验证；仅健康接口可用，后续文件业务仍未实现。

## 事实（已确认）
- GitHub 仓库：`cppppercy/lan-distribution-lab`，默认 main；最初为空。
- 用户环境：Windows、32 GB 内存，手机与平板两台移动设备，家庭 Wi-Fi 或手机热点。
- 用户确认局域网基本连接没有问题；项目专用服务的连通性/性能尚无运行记录。
- 编码协作：Codex 优先、DSH 备用，ChatGPT 制定需求和审核。
- 已完成文档：README、REQUIREMENTS、AGENTS、TASKS、DECISIONS、STATUS（以仓库实际提交为准）。
- 项目代码：最小 Python 包、仅本机监听的 Uvicorn 启动入口、`GET /api/health` 与 pytest：**IMPLEMENTED**。
- 文件传输、设备发现、续传、多客户端分发：**NOT_IMPLEMENTED**。
- GitHub Actions 工作流：**IMPLEMENTED，远端执行状态为 NOT_RUN（本次记录更新时尚未推送/创建 PR）**。
- 真实手机/平板测量、断线实验、性能结果：**NOT_RUN**。

## 当前任务
- **T001 — DONE**：最小工程初始化、健康接口、自动化测试、CI 配置和启动说明已按任务边界完成。完整证据见下方记录与 `docs/TASKS.md`。
- 下一关卡：G0 的本地工程基线已满足；GitHub CI 结论等待分支推送后的远端记录，不将其预先标为通过。

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
已知失败/未运行项：pytest 的 1 条警告来自 Starlette TestClient 使用 AnyIO 已弃用类型别名；不影响本次 2 项断言。GitHub Actions 远端状态在推送前为 NOT_RUN。
当前分支、工作区状态、PR：见本任务提交与推送后的 Git 记录；不在此处预填远端结果。
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
