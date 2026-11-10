# 实验记录

**状态**：T001 不执行性能、真实 Wi-Fi、手机或平板实验；这些项目当前均为 **NOT_RUN**。T008 提供本机受控并发的可复现执行入口与原始数据格式，但尚未执行策略对照实验，也不产生性能结论。本文件是后续任务记录可复现实验与原始数据的模板，不得将模板视为实验结果。

## 已登记但未执行的实验

| 实验 ID | 目的 | 状态 | 原始数据位置 |
| --- | --- | --- | --- |
| EXP-T001-LOCAL-HEALTH | 验证本机健康接口能被本机 HTTP 客户端调用 | PASS：2026-11-08，本机 `127.0.0.1:8000` 返回 `status=ok` | 无性能原始数据；命令见 `docs/STATUS.md` |
| EXP-G1-MOBILE-TRANSFER | 手机/平板与 Windows 主机的真实文件收发 | NOT_RUN（T003/T004 后） | 尚无 |
| EXP-G4-DISTRIBUTION | 不同分发策略、文件大小与断线恢复的对照 | NOT_RUN（T010 后） | 尚无 |

## T008 受控并发实验入口（本机）

以下命令用于在**本机**对相同文件、相同客户端数量依次执行三种策略，并把每次运行保存到独立目录，供 T010 分析前验证入口与原始数据格式。它们不代表真实 Wi-Fi/手机/平板实验，也不产生“哪种策略更快/更稳”的结论。

```powershell
# 同一服务端、同一 file_id、同一客户端数量（每个 --tokens-stdin 提供 N 行独立 token）
python -m lan_distribution_lab distribute --server http://127.0.0.1:8000 `
  --file-id <file_id> --strategy sequential --clients 5 `
  --output-dir .\exp\sequential --tokens-stdin

python -m lan_distribution_lab distribute --server http://127.0.0.1:8000 `
  --file-id <file_id> --strategy parallel --clients 5 `
  --output-dir .\exp\parallel --tokens-stdin

python -m lan_distribution_lab distribute --server http://127.0.0.1:8000 `
  --file-id <file_id> --strategy controlled --clients 5 --max-concurrent 2 `
  --output-dir .\exp\controlled --tokens-stdin
```

每次运行在各自目录产生：`distribution-result.json`（`schema_version: 2`；策略、`scheduling.max_concurrent`/`observed_max_active`、各客户端 `queued_at`/`wait_seconds`/`duration_seconds`/字节/状态、成功失败取消汇总）和 `distribution-runs.db`（`distribution_runs`/`distribution_clients`）。T010 应记录执行环境、文件大小、客户端数量、并发上限、重复轮次，并保留原始 JSON/SQLite；未执行或失败的轮次同样记录，不得据此提前宣称性能结论。

## 后续单次实验记录模板

```text
实验 ID：
日期与执行者：
关联任务 / Git SHA：
实验类型：本机模拟 / 真实 Wi-Fi / 手机热点（只选实际类型）
主机与客户端：操作系统、设备类型、浏览器或 CLI 版本
网络拓扑：SSID/热点说明、是否确认客户端互访；不记录密码或私人标识
服务配置：监听地址、端口、文件大小、并发数、策略、超时
变量与对照：
重复次数：
测量指标及单位：
原始数据文件及校验：
执行命令：
结果：成功、失败及其证据
限制与异常：
结论：仅由本记录可支持的结论
```

## 记录规则

- 本机多进程或本机 HTTP 测试只说明程序行为，不能标为真实多设备无线测试。
- 性能结论应保留原始 CSV/JSON、环境信息和至少三轮关键对照；未运行或失败的轮次同样记录。
- 不提交真实私人文件、访问令牌、Wi-Fi 密码或完整敏感日志。
