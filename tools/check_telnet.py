"""在电脑上直接测试路由器的 Telnet 与状态读取（不需要 Home Assistant）。

用法（Windows 命令行 / PowerShell）：
    python check_telnet.py                          # 默认 192.168.31.1 / root / 无密码
    python check_telnet.py 192.168.31.1             # 指定 IP（也可写 192.168.31.1:23）
    python check_telnet.py 192.168.31.1 root mima   # 指定用户名与密码

它会做三件事：
1. 用和集成里完全相同的代码连 Telnet、登录、执行巡检脚本；
2. 打印原始输出（能直接看出路由器缺哪些指标）；
3. 打印解析后的关键指标，确认数据能进 Home Assistant。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "custom_components", "xmluyouqiwenkong")
sys.path.insert(0, PKG)

from parser import parse_poll  # noqa: E402
from telnet_client import TelnetError, XiaomiTelnetClient  # noqa: E402


def _split_host(value: str, default_port: int = 23) -> tuple[str, int]:
    value = value.strip()
    for prefix in ("telnet://", "http://", "https://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
    value = value.split("/")[0]
    if value.count(":") == 1:
        head, _, tail = value.partition(":")
        if tail.isdigit():
            return head, int(tail)
    return value, default_port


async def _main() -> int:
    raw_host = sys.argv[1] if len(sys.argv) > 1 else "192.168.31.1"
    username = sys.argv[2] if len(sys.argv) > 2 else "root"
    password = sys.argv[3] if len(sys.argv) > 3 else ""
    host, port = _split_host(raw_host)

    print(f"→ 连接 {host}:{port}  用户={username}  密码={'已填' if password else '空'}")
    client = XiaomiTelnetClient(host=host, port=port, username=username, password=password, timeout=15)

    try:
        raw = await client.async_poll()
    except TelnetError as err:
        print(f"✗ 失败：{err}")
        print("  排查建议：1) ping 一下路由器；2) 确认 Telnet 已开启（23 端口）；3) 需要密码就补第 3 个参数。")
        return 1

    print("\n===== 原始输出（已剔除以太网/WWW 回显与标记）=====")
    print(raw)

    values, _counters = parse_poll(raw, None, time.time())
    print("===== 解析结果 =====")
    fields = [
        ("cpu_temperature", "CPU 温度 ℃"),
        ("cpu_temp_source", "温度来源温区"),
        ("cpu_usage", "CPU 占用率 %（首次为空，需两次采样）"),
        ("cpu_frequency", "CPU 频率 MHz"),
        ("load_1m", "1 分钟负载"),
        ("load_5m", "5 分钟负载"),
        ("load_15m", "15 分钟负载"),
        ("memory_usage", "内存占用率 %"),
        ("memory_available", "可用内存 MB"),
        ("memory_total", "内存总量 MB"),
        ("uptime", "运行时长 s"),
        ("connected_devices", "在线设备数"),
        ("wifi_clients", "Wi-Fi 终端数"),
        ("wifi_radios", "无线射频数"),
        ("storage_usage", "存储占用率 %"),
        ("storage_available_placeholder", "（占位）"),
        ("fan_speed", "风扇转速 rpm"),
        ("model", "型号"),
        ("firmware", "固件"),
        ("kernel", "内核"),
        ("hostname", "主机名"),
        ("processes", "进程数"),
    ]
    for key, label in fields:
        if key.endswith("placeholder"):
            continue
        print(f"  {label:<32} = {values.get(key)}")

    zones = values.get("thermal_zones") or {}
    if zones:
        print("  温区明细：")
        for zone_key, info in zones.items():
            print(f"    - {zone_key} ({info.get('type')}) = {info.get('temperature')} ℃")

    devices = values.get("devices") or []
    print(f"  在线设备（{len(devices)}）：")
    for device in devices[:20]:
        print(f"    - {device}")

    print("\n✓ 完成。把以上数值和米家/路由器后台对一下，就能确认采集正确。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
