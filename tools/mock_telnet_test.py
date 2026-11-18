"""端到端离线测试：在 127.0.0.1 起一个假的 Telnet 服务器，验证客户端的
登录流程、回显剔除、标记切分。不需要真实路由器。

    python mock_telnet_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "custom_components", "xmluyouqiwenkong"))

from parser import parse_poll  # noqa: E402
from telnet_client import (  # noqa: E402
    START_MARKER,
    TelnetAuthError,
    XiaomiTelnetClient,
)

PROMPT = b"root@XiaoQiang:~# "

OUTPUT = (
    START_MARKER
    + "\r\ncputemp_raw=62000\r\n"
    "zone=cpu-thermal|62000\r\n"
    "loadavg=0.15 0.22 0.20 2/412 12345\r\n"
    "uptime=98234.55 372211.22\r\n"
    "stations=12\r\n"
    "@@XQLYQ_POLL_END@@\r\n" + PROMPT.decode()
).encode()

IAC, WILL, ECHO = 255, 251, 1

FAILED: list[str] = []


def check(label, condition):
    print(f"[{'OK  ' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILED.append(label)


async def handler(reader, writer, *, need_password: bool, password: str) -> None:
    """模拟 telnetd：先协商，再按场景决定是否要求登录，之后像 PTY 一样回显。"""
    writer.write(bytes((IAC, WILL, ECHO)))
    if not need_password:
        writer.write(PROMPT)
        await writer.drain()
    else:
        writer.write(b"\r\nlogin: ")
        await writer.drain()

    buf = b""
    got_password = not need_password
    answered = False

    while True:
        data = await reader.read(1024)
        if not data:
            break
        writer.write(data)  # 模拟终端回显（这正是需要被剔除的噪声）
        await writer.drain()
        buf += data

        # 登录阶段：一碰到换行就当作提交
        while b"\n" in buf and not got_password:
            line, _, buf = buf.partition(b"\n")
            line = line.replace(b"\r", b"").strip()
            if not line:
                continue
            writer.write(b"Password: ")
            await writer.drain()
            # 下一行是密码
            while True:
                more = await reader.read(1024)
                if not more:
                    return
                writer.write(more)
                await writer.drain()
                pw_buf = more
                if b"\n" not in pw_buf:
                    continue
                pw_line, _, _ = pw_buf.partition(b"\n")
                pw = pw_line.replace(b"\r", b"").decode("utf-8", "ignore").strip()
                if pw == password:
                    got_password = True
                    buf = b""
                    writer.write(PROMPT)
                    await writer.drain()
                else:
                    writer.write(b"Login incorrect\r\nlogin: ")
                    await writer.drain()
                break

        if got_password and not answered and b"_POLL_END@@" in buf:
            answered = True
            writer.write(OUTPUT)
            await writer.drain()


async def scenario(need_password: bool, password: str, client_password: str):
    server = await asyncio.start_server(
        lambda r, w: handler(r, w, need_password=need_password, password=password),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    client = XiaomiTelnetClient(
        host="127.0.0.1", port=port, username="root", password=client_password, timeout=8
    )
    try:
        return await client.async_poll()
    finally:
        server.close()
        await server.wait_closed()


def main() -> int:
    print("== 场景 1：免密码直接进 shell ==")
    raw = asyncio.run(scenario(False, "", ""))
    print("接收文本 repr：", repr(raw[:120]), "...")
    check("剔除提示符回显", "root@XiaoQiang" not in raw)
    check("剔除命令回显", "printf" not in raw)
    check("保留真实输出", "cputemp_raw=62000" in raw)
    values, _ = parse_poll(raw, None, 1.0)
    check("解析出 CPU 温度 62.0", values["cpu_temperature"] == 62.0)
    check("解析出 Wi-Fi 终端数 12", values["wifi_clients"] == 12)

    print("\n== 场景 2：需要密码，密码正确 ==")
    raw2 = asyncio.run(scenario(True, "mima123", "mima123"))
    check("完成登录并拿到数据", "cputemp_raw=62000" in raw2)

    print("\n== 场景 3：需要密码，密码错误 ==")
    try:
        asyncio.run(scenario(True, "mima123", "wrong"))
    except TelnetAuthError as err:
        check(f"抛出认证错误（{err}）", True)
    else:
        check("抛出认证错误", False)

    print("\n== 场景 4：有 login 提示、root 密码为空（集成按一次回车通过）==")
    raw4 = asyncio.run(scenario(True, "", ""))
    check("空密码登录后拿到数据", "cputemp_raw=62000" in raw4)
    values4, _ = parse_poll(raw4, None, 1.0)
    check("空密码场景数据可用", values4["cpu_temperature"] == 62.0)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过：Telnet 客户端流程正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
