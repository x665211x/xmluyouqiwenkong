"""离线自检：用一段模拟的路由器输出验证解析逻辑（不需要路由器、不需要 HA）。

    python selftest.py

会检查：温度归一化（milli-°C → ℃）、CPU 占用率差分、内存占用率、
存储占用率、ARP 设备数、风扇转速、Wi-Fi 终端数、网卡速率差分。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "custom_components", "xmluyouqiwenkong"))

from parser import parse_poll  # noqa: E402

SAMPLE_1 = """@@XQLYQ_START@@
cputemp_raw=62000
zone=cpu-thermal|62000
zone=ddr-thermal|48000
zone=wifi-thermal|51000
loadavg=0.15 0.22 0.20 2/412 12345
stat=cpu  15401 22 3001 98221 331 0 210 0 0 0;cpu0 1500 4 300 9800 30 0 20 0 0 0
meminfo=MemTotal: 512000 kB;MemFree: 120000 kB;MemAvailable: 260000 kB;Buffers: 5000 kB;Cached: 90000 kB;SwapTotal: 0 kB;SwapFree: 0 kB
uptime=98234.55 372211.22
netdev=Inter-|   Receive                                                |  Transmit; face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed;    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0;  eth0: 1200000 800 0 0 0 0 0 0 900000 700 0 0 0 0 0 0;
arp=IP address       HW type     Flags       HW address            Mask     Device;192.168.31.100    0x1         0x2         AA:BB:CC:DD:EE:01     *        br-lan;192.168.31.101    0x1         0x2         AA:BB:CC:DD:EE:02     *        br-lan;192.168.31.102    0x1         0x0         00:00:00:00:00:00     *        br-lan;
dfroot=/dev/mtdblock6 4096 1024 3072 25% /
cpufreq=1000000
fan=cpu_thermal|1500
model=Xiaomi Router 10000
version=1.0.65
hostname=XiaoQiang
kernel=4.4.60
procs=120
radios=3
stations=12
@@XQLYQ_POLL_END@@
"""

SAMPLE_2 = """@@XQLYQ_START@@
cputemp_raw=63000
zone=cpu-thermal|63000
zone=ddr-thermal|49000
zone=wifi-thermal|52000
loadavg=0.20 0.25 0.22 3/415 12400
stat=cpu  15601 22 3101 98400 340 0 210 0 0 0;cpu0 1520 4 310 9810 31 0 20 0 0 0
meminfo=MemTotal: 512000 kB;MemFree: 118000 kB;MemAvailable: 258000 kB;Buffers: 5000 kB;Cached: 90000 kB;SwapTotal: 0 kB;SwapFree: 0 kB
uptime=98434.55 372411.22
netdev=Inter-|   Receive                                                |  Transmit; face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed;    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0;  eth0: 4260000 1900 0 0 0 0 0 0 3900000 1500 0 0 0 0 0 0;
arp=IP address       HW type     Flags       HW address            Mask     Device;192.168.31.100    0x1         0x2         AA:BB:CC:DD:EE:01     *        br-lan;
dfroot=/dev/mtdblock6 4096 1100 3016 26% /
cpufreq=1000000
fan=cpu_thermal|1600
model=Xiaomi Router 10000
version=1.0.65
hostname=XiaoQiang
kernel=4.4.60
procs=121
radios=3
stations=13
@@XQLYQ_POLL_END@@
"""

_FAILED: list[str] = []


def check(label: str, actual, expected, tolerance: float = 0.06) -> None:
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        ok = abs(float(actual) - expected) <= tolerance
    else:
        ok = actual == expected
    status = "OK  " if ok else "FAIL"
    print(f"[{status}] {label}: 期望={expected!r} 实际={actual!r}")
    if not ok:
        _FAILED.append(label)


def main() -> int:
    values, counters = parse_poll(SAMPLE_1, None, 1000.0)

    check("CPU 温度（milli→℃）", values["cpu_temperature"], 62.0, 0.11)
    check("温度来源", values["cpu_temp_source"], "cpu-thermal")
    check("温区数量", len(values["thermal_zones"]), 3)
    check("1 分钟负载", values["load_1m"], 0.15)
    check("运行中的进程/总进程", values["processes_running"], 2)
    check("内存总量 MB", values["memory_total"], 500.0, 0.2)
    check("可用内存 MB", values["memory_available"], 253.9, 0.2)
    check("内存占用率 %", values["memory_usage"], 49.2, 0.2)
    check("运行时长 s", values["uptime"], 98234.55, 0.1)
    check("累计下载 MB", values["rx_total"], 1.1, 0.2)
    check("累计上传 MB", values["tx_total"], 0.9, 0.2)
    check("在线设备数（排除 0x0 与全零 MAC）", values["connected_devices"], 2)
    check("存储占用率 %", values["storage_usage"], 25.0)
    check("存储总量 MB", values["storage_total"], 4.0, 0.1)
    check("CPU 频率 MHz", values["cpu_frequency"], 1000.0, 1)
    check("风扇转速", values["fan_speed"], 1500)
    check("Wi-Fi 终端数", values["wifi_clients"], 12)
    check("无线射频数", values["wifi_radios"], 3)
    check("型号", values["model"], "Xiaomi Router 10000")
    check("首次 CPU 占用率为空（需两次采样）", values["cpu_usage"], None)
    check("首次速率为空", values["rx_rate"], None)

    # 第二次采样：验证差分
    values2, _ = parse_poll(SAMPLE_2, counters, 1030.0)
    # stat 累计时间片 117186 → 117674（Δ488），其中 idle 98552 → 98740（Δ188），忙 Δ300
    expected_cpu = 300 / 488 * 100
    check("CPU 占用率差分", values2["cpu_usage"], round(expected_cpu, 1), 0.2)
    # rx 增加 3,060,000 字节 / 30s / 1024 = 99.6 kB/s
    check("下载速率 kB/s", values2["rx_rate"], round(3060000 / 30 / 1024, 1), 0.5)
    check("上传速率 kB/s", values2["tx_rate"], round(3000000 / 30 / 1024, 1), 0.5)

    print()
    if _FAILED:
        print(f"✗ {len(_FAILED)} 项失败：{', '.join(_FAILED)}")
        return 1
    print("✓ 全部通过：解析逻辑正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
