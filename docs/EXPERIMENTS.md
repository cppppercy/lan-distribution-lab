# 实验记录

**状态**：电脑、手机、平板的 G1 基础文件收发、1 MiB 往返哈希与功能性并发已在真实局域网中完成；细节见 [课程实验报告](EXPERIMENT_REPORT.md) 和 [项目状态](STATUS.md)。T010 已提供控制器侧的重复实验导出，T011 已提供只读分析和 SVG 图表；正式 G4 三策略无线性能数据、真实 UDP 广播和 Wireshark 抓包仍未采集。

## 实验登记与状态

| 实验 ID | 目的 | 状态 | 原始数据位置 |
| --- | --- | --- | --- |
| EXP-T001-LOCAL-HEALTH | 验证本机健康接口能被本机 HTTP 客户端调用 | PASS：2026-11-08，本机 `127.0.0.1:8000` 返回 `status=ok` | 无性能原始数据；命令见 `docs/STATUS.md` |
| EXP-G1-MOBILE-TRANSFER | 电脑、手机、平板的配对、文件收发、1 MiB 往返 SHA-256、功能性并发与 Wi-Fi 中断观察 | PASS（2026-11-13，限已记录项目） | 本地私有验收截图及 CSV/SQLite 摘要；公开展示见 [实验报告](EXPERIMENT_REPORT.md) |
| EXP-G4-DISTRIBUTION | 不同分发策略、文件大小与断线恢复的对照 | NOT_RUN（T010 工具已实现，尚未采集） | 未来由 `experiment` 新目录保留 |
| EXP-G3-UDP-DISCOVERY | 真实 Wi-Fi/移动热点上的 UDP 广播发现、多网卡选择与防火墙影响 | NOT_RUN（T009 后） | 本机 loopback 自动化通过；真实广播尚无 |

## T009 UDP 发现实验（待执行，真实网络）

本机 loopback 自动化测试（`tests/test_udp_discovery.py`）只证明协议、校验与 CLI 行为，**不等于**真实广播发现。后续人工实验应记录：设备与 Windows 版本、网络拓扑（家庭 Wi-Fi/移动热点）、网卡数量与所选 `--broadcast`/`--interface`、Windows 防火墙状态（是否手动放行 UDP）、服务端 `--discover-port`、发现耗时与结果数、失败现象（超时/被隔离），以及手机/平板是否能手动打开发现的 URL。实验完成前保持 **NOT_RUN**，并按下方模板记录原始数据。

## T010 分发实验驱动（已实现工具；真实实验待执行）

实验驱动只协调同一 Windows 控制器内的逻辑客户端下载；认证仍需每个逻辑客户端一个独立会话。以下命令会提示输入三个无回显的一次性 `read` 配对码，并以固定顺序执行 3 轮 × 1 文件 × 3 策略。开始前为同一个服务生成三个独立 read 配对码；不要把配对码或 Bearer token 写入本文件、终端历史、CSV 或 Git。

```powershell
python -m lan_distribution_lab experiment `
  --server http://127.0.0.1:8000 `
  --file-id <FILE_UUID> `
  --strategies sequential,parallel,controlled `
  --clients 3 --max-concurrent 2 --rounds 3 `
  --environment-type local_loopback `
  --output-dir .\experiments\exp-20261110-local-001 `
  --pair
```

`--file-id` 可重复使用以加入不同大小对象；试验顺序始终为 `round → file_id → strategy`，便于重跑、不会产生未记录随机性。`--on-error continue`（默认）持久化失败后继续，`--on-error stop` 会将未开始的计划标为 `cancelled`。每次指定的 `--output-dir` 必须从未存在，避免覆盖旧数据或把已完成文件当成新网络下载。

### 原始数据目录与字段

```text
<output-dir>/
  experiments.json                       # 清单、环境/软件版本、trial 索引、描述性统计
  trials.csv                             # 每 trial 一行，失败/取消也保留
  clients.csv                            # 每 trial × 逻辑客户端一行
  trials/trial-001/distribution-result.json  # 仅成功调用的原始 DistributionRun
  trials/trial-001/client-1/download.bin
  ...
```

- `experiments.json`：`experiment_id`、格式版本、UTC 开始/更新时间、服务器、文件/策略/客户端/超时/重试配置、`environment_type`、固定顺序、Git commit、Python、平台、每轮索引和状态。它不含凭据、Cookie、配对码或本地绝对路径。
- `trials.csv`：`size_bytes`、成功/失败/取消计数、`observed_max_active`、`logical_completed_bytes`、`resumed_bytes`、状态和安全错误类别；`application_payload_bytes = succeeded × size_bytes`，`application_payload_throughput_Bps = application_payload_bytes / duration_seconds`。后者是应用层有效载荷按批次墙钟时间的 B/s，不能称作链路带宽。
- `clients.csv`：匿名 `client_id`、状态、排队/执行秒数、下载/复用字节、错误类别、`integrity_verified`。失败客户端不会静默删除。
- 成功样本统计仅纳入状态为 `completed`（全部逻辑客户端成功）的 trial；成功率分母仍是全部 trial。均值、中位数、最小/最大只作描述，不推断显著性，也不宣称 controlled 一定更好。

### 真实性与后续人工实验

`local_loopback` 仅表示控制器和服务都位于本机；`controller_to_lan` 表示控制器访问了 LAN 上的服务，但逻辑客户端仍都在控制器内。两种自动化环境都**不是**真实独立物理客户端或无线性能实验。T010 没有请求、也没有执行手机、平板、家庭 Wi-Fi、热点、校园 Wi-Fi、断线或吞吐量实测；它们保持 **NOT_RUN**。

G4 人工实验开始时，应对每一个结果目录按模板补充：服务端与每台客户端的型号/系统/浏览器或 CLI 版本，网络拓扑与 IP 网段（不写 Wi-Fi 密码）、文件 UUID/大小/SHA-256、客户端数、策略/上限、轮数、实际环境标签、时间、断线方式、失败现象和原始目录校验。关键固定变量对照至少三轮，且不得把不同文件大小、客户端数或网络环境混为同一对照组。

## T011 分析与图表（已实现工具；真实数据待执行）

对每个已完成的 T010 目录运行一次只读分析；输出目录必须不存在。分析会复核 schema、experiment/trial ID、计划数量、CSV/原始 JSON 的状态计数、文件 ID/大小/SHA-256、有限时间/字节值和失败轮次边界。任何矛盾、重复 ID、NaN/∞、缺失成功原始 JSON 或不兼容版本都会拒绝报告，不会用缺失数据补图。

```powershell
python -m lan_distribution_lab analyze `
  --input .\experiments\exp-20261110-local-001 `
  --output-dir .\analysis\exp-20261110-local-001
```

图表以 SVG 写入 `charts/`：批次完成时间、应用层有效吞吐量、完整成功率、客户端等待/执行时间；只有在固定文件版本/大小、客户端数、环境、超时、重试、受控并发配置和**脱敏服务器身份**的可比组中才并列策略。controlled 上限关系图要求至少两个不同上限且其余条件（包括同一服务器身份）可比。报告只保留稳定 `origin-sha256:<短摘要>`，不回显私人服务器地址。所有图标明单位和样本量；无成功样本则写 `unavailable`，不绘制伪零值。

- **批次完成时间**：`trials.csv.duration_seconds`，只对完整成功 trial 做均值/中位数/最小/最大。
- **应用层有效吞吐量**：`application_payload_throughput_Bps`，转换 MiB/s 使用 `B / 1024 / 1024`。这是成功应用载荷除以批次墙钟时间，不是 TCP、网卡或无线链路带宽。
- **完成率**：完整成功 trial / 全部计划 trial；失败、部分成功和取消均保留在分母/异常记录中。
- **并发与等待**：`max_concurrent` 是配置，`observed_max_active` 是控制器进程观测；`wait_seconds` 是排队，客户端 `duration_seconds` 是执行，二者不能混用。
- **Jain 公平性**：完整 trial 内每个成功客户端 `x_i=downloaded_bytes/duration_seconds`（B/s），`J=(Σx_i)^2/(nΣx_i^2)`。少于两个、零/缺失/非有限样本即为 unavailable；它不是 TCP 层公平性。

报告严格分为原始数据可直接观察的结果、可能的技术解释和仍待抓包/真实网络验证的假设；不产生显著性、因果或“某策略必然更好”的结论。

### 抓包证据

T011 不会自动抓包、不会读取 PCAP，也不要求 CI 安装 Wireshark。Windows Wireshark 操作、`tcp.port == <port>`、`http.response.code == 206`、`tcp.analysis.retransmission` 等过滤器及隐私限制见 [抓包证据指南](PACKET_CAPTURE_GUIDE.md)。可选的 `--packet-summary` 仅接受手工离线整理的字段白名单 JSON；没有该摘要时报告明确显示没有 TCP/HTTP 包级证据。TCP 重传标记不等于应用失败/物理断网，HTTP Range/If-Range 也不等于 TCP 重传。

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
