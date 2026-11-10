# T011 抓包证据指南（Windows / Wireshark）

**状态**：方法已提供；没有自动监听、PCAP 解析或真实抓包结果。原始 PCAP 默认只由操作者在本机保存，禁止提交到 Git。

## 安全与范围

项目当前使用 HTTP。PCAP 可能含配对码、Bearer Token、Cookie、文件内容、IP 和设备信息；因此不要在公共网络抓包，不要上传未清理 PCAP，也不要把 Authorization、Cookie、请求体、文件名或完整载荷贴入报告。每次只选实际参与实验的一张网卡和明确时间窗口；分析前记录实验 `experiment_id`、`trial_id`、服务 IP/端口、开始/结束时间和 file_id（不记录 token）。

## 手动步骤

1. 在 Wireshark 选择服务实际使用的 Wi-Fi、以太网或移动热点网卡；不要选择“所有接口”。
2. 开始抓包后立刻运行一次 T010 `experiment`，结束后立即停止。保存原始 PCAPNG 到本机私有目录，文件名仅含日期和实验 ID。
3. 先用服务实际端口替换下列示例的 `8000`，再结合服务 IP 与 trial 时间窗口缩小范围。
4. 只导出脱敏的字段白名单摘要；分析 CLI 不读取 PCAP/PCAPNG，也不会启动 tshark 或上传数据。

## 显示过滤器与解释

| 目的 | 示例过滤器 | 可支持的观察 | 不能直接推出 |
| --- | --- | --- | --- |
| 关联服务 | `tcp.port == 8000` | 该端口的 TCP 会话 | 其他连接是否属于本次 trial（仍要核对时间/IP） |
| 三次握手 | `tcp.flags.syn == 1` | SYN/SYN-ACK/ACK 序列 | 应用层下载一定成功 |
| 连接关闭 | `tcp.flags.fin == 1 or tcp.flags.reset == 1` | FIN/RST 证据 | 物理断网原因 |
| HTTP | `http` | HTTP/1.1 请求与响应 | TCP 总吞吐量 |
| Range | `http.range or http.response.code == 206` | 请求 Range、响应 206 | TCP 重传或应用层失败 |
| 不可满足范围 | `http.response.code == 416` | 服务器拒绝的范围 | 客户端文件一定损坏的原因 |
| 重传标记 | `tcp.analysis.retransmission` | Wireshark 的启发式标记数 | 丢包率、物理断网次数或应用失败数 |

`If-Range` 是 HTTP 资源版本保护：只有强 ETag 匹配时，服务才可返回安全的 `206` 片段；它不等同于 TCP 的可靠重传。TCP 重传是字节流层恢复机制，不能由 `trials.csv` 的失败数反推；反之，一个 HTTP Range 恢复也不表示检测到 TCP 重传。

## RTT、吞吐量与并发

Wireshark 的 TCP RTT 图或字段必须标记其具体工具/字段和会话范围；它是 TCP 分段层估计，不能与 T010 的 `duration_seconds` 直接等同。T010 的 `application_payload_throughput_Bps` 是“成功应用载荷 / 批次墙钟时间”，不是网卡、TCP payload 或链路带宽。并发连接数也不必等于 T010 客户端任务数：连接可能复用、重试或已经关闭。

## 可选离线摘要

如本机安装 tshark，操作者可手动导出**仅限**实验窗口的 HTTP 状态计数、`tcp.analysis.retransmission` 计数和注明方法的 RTT 值，整理为下列 JSON 后显式传给 `analyze --packet-summary`：

```json
{
  "schema_version": 1,
  "observations": [{
    "window_id": "trial-window-1",
    "http_status_counts": {"200": 2, "206": 1, "416": 0},
    "tcp_retransmission_packets": 0,
    "rtt_method": "Wireshark TCP analysis field",
    "rtt_seconds": 0.012
  }]
}
```

分析器拒绝未列入白名单的字段，尤其是认证头、Cookie、IP、文件路径和载荷。缺少摘要时报告明确显示“没有抓包证据”，不会杜撰 TCP/HTTP 结论。
