"""把路由器返回的 KEY=value 文本解析成 Home Assistant 需要的状态字典。

纯函数、无 Home Assistant 依赖，方便单独测试（tools/selftest.py 会用到）：
    values   —— 可直接展示的状态（温度、占用率、速率、设备列表…）
    counters —— 下一次求差值需要的累计量（CPU 时间片、网卡字节数、时间戳）
"""

from __future__ import annotations

import re
from typing import Any

END_MARKER = "@@XQLYQ_POLL_END@@"

_KV_RE = re.compile(r"^([A-Za-z0-9_]+)=(.*)$", re.MULTILINE)
_CPU_LINE_RE = re.compile(r"cpu\s+((?:\d+\s*)+)")

KB = 1024.0
MB = 1024.0 * 1024.0


# ------------------------------------------------------------------ 小工具
def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.replace("\x00", "").strip()
    return cleaned or None


def normalize_temperature(raw: Any) -> float | None:
    """内核温度大多是千分之一摄氏度（45000 = 45.0℃），个别平台直接给摄氏度。"""
    value = _to_float(raw)
    if value is None:
        return None
    if value > 150:  # 45℃ 不会写成 450，超过 150 必然是 milli-°C
        value = value / 1000.0
    if value <= 0 or value > 200:  # 明显异常（传感器未就绪 / 读错节点）
        return None
    return round(value, 1)


def _parse_pairs(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """把 KEY=value 行拆成「单值」与「多值」两类。"""
    scalar: dict[str, str] = {}
    multi: dict[str, list[str]] = {}
    for match in _KV_RE.finditer(text):
        key, value = match.group(1), match.group(2).strip()
        if key in ("zone", "fan"):
            multi.setdefault(key, []).append(value)
        else:
            scalar[key] = value
    return scalar, multi


# ------------------------------------------------------------------ 主入口
def parse_poll(
    text: str,
    prev: dict[str, Any] | None = None,
    now: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """解析一次巡检输出，返回 (values, counters)。"""
    if END_MARKER in text:
        text = text.split(END_MARKER, 1)[0]
    scalar, multi = _parse_pairs(text)
    values: dict[str, Any] = {}

    # ---- 温度：逐 zone 解析，再挑一个代表 CPU/SOC --------------------
    zones: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(multi.get("zone", [])):
        if "|" in entry:
            raw_type, raw_temp = entry.split("|", 1)
        else:
            raw_type, raw_temp = f"zone{index}", entry
        temp = normalize_temperature(raw_temp)
        if temp is None:
            continue
        zone_key = (raw_type or f"zone{index}").strip()
        if zone_key in zones:
            zone_key = f"{zone_key}_{index}"
        zones[zone_key] = {"type": raw_type.strip() or zone_key, "temperature": temp}

    cpu_zone: str | None = None
    for key in zones:
        lowered = key.lower()
        if ("cpu" in lowered or "soc" in lowered) and "wifi" not in lowered:
            cpu_zone = key
            break
    if cpu_zone is None and zones:
        cpu_zone = max(zones, key=lambda k: zones[k]["temperature"])

    cpu_temp = zones[cpu_zone]["temperature"] if cpu_zone else normalize_temperature(
        scalar.get("cputemp_raw")
    )
    values["cpu_temperature"] = cpu_temp
    values["cpu_temp_source"] = cpu_zone
    values["thermal_zones"] = zones

    # ---- 负载 -------------------------------------------------------
    load_parts = (scalar.get("loadavg") or "").split()
    for index, key in ((0, "load_1m"), (1, "load_5m"), (2, "load_15m")):
        values[key] = _to_float(load_parts[index]) if len(load_parts) > index else None
    if len(load_parts) >= 4 and "/" in load_parts[3]:
        running, total = load_parts[3].split("/", 1)
        values["processes_running"] = _to_int(running)
        values["processes_total"] = _to_int(total)

    # ---- CPU 时间片（差分算占用率） ----------------------------------
    stat_text = (scalar.get("stat") or "").replace(";", " ")
    cpu_total = cpu_busy = None
    stat_match = _CPU_LINE_RE.search(stat_text)
    if stat_match:
        ticks = [int(x) for x in stat_match.group(1).split()]
        if len(ticks) >= 4:
            idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
            cpu_total = sum(ticks)
            cpu_busy = cpu_total - idle
    values["cpu_usage"] = None

    # ---- 内存 -------------------------------------------------------
    meminfo: dict[str, int] = {}
    for part in (scalar.get("meminfo") or "").split(";"):
        if ":" not in part:
            continue
        name, raw = part.split(":", 1)
        digits = re.sub(r"[^0-9]", "", raw)
        if digits:
            meminfo[name.strip()] = int(digits)
    total_kb = meminfo.get("MemTotal")
    avail_kb = meminfo.get("MemAvailable")
    if avail_kb is None and meminfo.get("MemFree") is not None:
        avail_kb = (
            meminfo.get("MemFree", 0)
            + meminfo.get("Buffers", 0)
            + meminfo.get("Cached", 0)
        )
    values["memory_total"] = round(total_kb / KB, 1) if total_kb else None
    values["memory_available"] = round(avail_kb / KB, 1) if avail_kb else None
    values["memory_usage"] = (
        round((total_kb - avail_kb) / total_kb * 100, 1)
        if total_kb and avail_kb is not None
        else None
    )

    # ---- 运行时长 ---------------------------------------------------
    uptime_parts = (scalar.get("uptime") or "").split()
    values["uptime"] = _to_float(uptime_parts[0]) if uptime_parts else None

    # ---- 网络累计流量 -----------------------------------------------
    rx_bytes = tx_bytes = 0
    net_seen = False
    for line in (scalar.get("netdev") or "").split(";"):
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        if name.strip() in ("", "lo"):
            continue
        nums = rest.split()
        if len(nums) < 9:
            continue
        try:
            rx_bytes += int(nums[0])
            tx_bytes += int(nums[8])
            net_seen = True
        except ValueError:
            continue
    values["rx_total"] = round(rx_bytes / MB, 1) if net_seen else None
    values["tx_total"] = round(tx_bytes / MB, 1) if net_seen else None
    values["rx_rate"] = None
    values["tx_rate"] = None

    # ---- 在线设备（ARP 表） -----------------------------------------
    devices: list[str] = []
    for line in (scalar.get("arp") or "").split(";"):
        fields = line.split()
        if len(fields) >= 6 and fields[2].lower() == "0x2":
            mac = fields[3].upper()
            if mac in ("00:00:00:00:00:00", ""):
                continue
            devices.append(f"{fields[0]} {mac}")
    values["connected_devices"] = len(devices)
    values["devices"] = sorted(devices)

    # ---- 存储 -------------------------------------------------------
    df_tokens = (scalar.get("dfroot") or "").split()
    values["storage_total"] = values["storage_used"] = values["storage_usage"] = None
    if len(df_tokens) >= 3:
        total_1k = _to_int(df_tokens[1])
        used_1k = _to_int(df_tokens[2])
        if total_1k and used_1k is not None:
            values["storage_total"] = round(total_1k / KB, 1)
            values["storage_used"] = round(used_1k / KB, 1)
            values["storage_usage"] = round(used_1k / total_1k * 100, 1)

    # ---- CPU 频率 / 风扇 ---------------------------------------------
    cpufreq_khz = _to_float(scalar.get("cpufreq"))
    values["cpu_frequency"] = round(cpufreq_khz / 1000, 0) if cpufreq_khz else None

    fans: list[dict[str, Any]] = []
    for entry in multi.get("fan", []):
        if "|" not in entry:
            continue
        name, raw = entry.split("|", 1)
        rpm = _to_int(raw)
        if rpm is None or rpm <= 0:
            continue
        fans.append({"name": name.strip() or "fan", "rpm": rpm})
    values["fans"] = fans
    values["fan_speed"] = fans[0]["rpm"] if fans else None
    values["fan_name"] = fans[0]["name"] if fans else None

    # ---- 文字信息 ---------------------------------------------------
    values["model"] = _clean(scalar.get("model"))
    values["firmware"] = _clean(scalar.get("version"))
    values["hostname"] = _clean(scalar.get("hostname"))
    values["kernel"] = _clean(scalar.get("kernel"))
    values["processes"] = _to_int(scalar.get("procs"))
    values["wifi_radios"] = _to_int(scalar.get("radios"))
    values["wifi_clients"] = _to_int(scalar.get("stations"))

    counters: dict[str, Any] = {
        "cpu_total": cpu_total,
        "cpu_busy": cpu_busy,
        "rx_bytes": rx_bytes if net_seen else None,
        "tx_bytes": tx_bytes if net_seen else None,
        "ts": now,
    }

    # ---- 与上一次比较，得出占用率与实时速率 -------------------------
    if prev:
        prev_total = prev.get("cpu_total")
        prev_busy = prev.get("cpu_busy")
        if cpu_total and cpu_busy is not None and prev_total and prev_busy is not None:
            delta_total = cpu_total - prev_total
            delta_busy = cpu_busy - prev_busy
            if delta_total > 0:
                values["cpu_usage"] = round(max(0.0, delta_busy / delta_total * 100), 1)

        prev_ts = prev.get("ts")
        if net_seen and now and prev_ts and prev.get("rx_bytes") is not None:
            interval = now - prev_ts
            if interval > 0:
                values["rx_rate"] = round(
                    max(0.0, (rx_bytes - prev["rx_bytes"]) / interval / KB), 1
                )
                values["tx_rate"] = round(
                    max(0.0, (tx_bytes - prev["tx_bytes"]) / interval / KB), 1
                )

    return values, counters
