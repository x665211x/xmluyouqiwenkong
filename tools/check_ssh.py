"""在电脑上直接测试路由器的 SSH 通道与状态读取（不需要 Home Assistant）。

用法（Windows PowerShell）：
    python -m pip install paramiko                     # 只装一次
    python tools\\check_ssh.py                         # 默认 192.168.1.1:22 / root / 无密码
    python tools\\check_ssh.py 192.168.1.1             # 指定 IP
    python tools\\check_ssh.py 192.168.1.1 root mima   # 指定用户名与密码
    python tools\\check_ssh.py 192.168.1.1 root mima --port 2222 --raw

它做的事和集成里完全一样：用同一段巡检脚本取数，再用同一个解析器算指标，
所以这里能出数，HA 里选 SSH 就一定能出数。
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "custom_components", "xmluyouqiwenkong")
sys.path.insert(0, PKG)

from parser import parse_poll  # noqa: E402
from telnet_client import (  # noqa: E402
    END_MARKER,
    POLL_SCRIPT,
    XiaomiTelnetClient,
)

try:
    import paramiko
except ImportError:  # pragma: no cover
    print("需要先安装 paramiko：python -m pip install paramiko")
    sys.exit(2)


def _pick(data: dict, key: str, unit: str = "") -> str:
    value = data.get(key)
    if value in (None, "", {}):
        return "（路由器没给这个值）"
    return f"{value}{unit}"


def main() -> int:
    parser = argparse.ArgumentParser(description="测试小米路由器 SSH 取数")
    parser.add_argument("host", nargs="?", default="192.168.1.1")
    parser.add_argument("username", nargs="?", default="root")
    parser.add_argument("password", nargs="?", default="")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--raw", action="store_true", help="打印原始输出")
    args = parser.parse_args()

    host = args.host.strip()
    if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        host, args.port = host.rsplit(":", 1)[0], int(host.rsplit(":", 1)[1])

    print(f"→ 连接 {host}:{args.port}（用户 {args.username}）")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=args.port,
            username=args.username,
            password=args.password or None,
            timeout=args.timeout,
            auth_timeout=args.timeout,
            banner_timeout=args.timeout,
            allow_agent=False,
            look_for_keys=False,
        )
    except (paramiko.AuthenticationException, paramiko.SSHException) as err:
        text = str(err).lower()
        if isinstance(err, paramiko.AuthenticationException) or "auth" in text or "password" in text:
            print(f"✗ 失败：SSH 认证被拒绝（用户名或密码不对，或密码留空）：{err}")
            return 1
        print(f"✗ 失败：连不上 {host}:{args.port} → {err}")
        return 1
    except Exception as err:  # noqa: BLE001
        print(f"✗ 失败：连不上 {host}:{args.port} → {err}")
        print("  检查：dropbear 有没有开（netstat -lnt | grep 22）、端口对不对、防火墙是否放行")
        return 1

    try:
        _stdin, stdout, stderr = client.exec_command(POLL_SCRIPT, timeout=args.timeout + 8)
        raw = stdout.read().decode("utf-8", "ignore")
        err_text = stderr.read().decode("utf-8", "ignore")
    finally:
        client.close()

    if args.raw:
        print("\n---- 原始输出 ----")
        print(raw)
    if END_MARKER not in raw:
        print("✗ 失败：没拿到巡检结果")
        if err_text.strip():
            print(f"  路由器返回的错误：{err_text.strip()[:300]}")
        return 1

    data, _counters = parse_poll(XiaomiTelnetClient._extract(raw))
    print("\n---- 解析结果 ----")
    print("CPU 温度      :", _pick(data, "cpu_temperature", " ℃"))
    print("CPU 占用率    :", _pick(data, "cpu_usage", " %"))
    print("内存占用率    :", _pick(data, "memory_used_percent", " %"))
    print("在线设备数    :", _pick(data, "online_devices"))
    print("Wi-Fi 终端数  :", _pick(data, "wifi_clients"))
    print("运行时长      :", _pick(data, "uptime", " 秒"))
    print("风扇转速      :", _pick(data, "fan_rpm", " rpm"))
    print("\n✓ 成功：SSH 通道可用，集成里把「连接方式」改成 SSH 即可")
    return 0


if __name__ == "__main__":
    sys.exit(main())
