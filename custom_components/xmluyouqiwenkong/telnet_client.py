"""小米路由器 Telnet 客户端（纯标准库 asyncio 实现）。

为什么不用 telnetlib：
    Python 3.13 起 telnetlib 已从标准库移除，Home Assistant 也早已不再依赖它。
    这里用 asyncio.open_connection 自己做最小实现，既能跑在 HA 里，
    也能单独在电脑上跑（tools/check_telnet.py 就是复用它）。

设计要点：
1. 一次连接只发一段"巡检脚本"，脚本把每个指标打印成  KEY=value  一行，
   末尾打印结束标记；读到标记就断开 —— 轮询开销最小、解析最稳。
2. 开始/结束标记在脚本文本里都以「变量拆开」的形式出现
   （echo "${S}_START@@"），因此即使路由器把输入回显回来，
   完整标记也只会在真正的输出里出现，不会被回显误匹配。
3. 自动处理 Telnet 协议协商（IAC / DO / DONT / WILL / WONT / SB），
   一律拒绝对方选项（不回显、不用 NAWS），让输出保持干净。
4. 自动适配两种登录形态：
   - 直接给 root shell（大部分已开启 Telnet 的小米路由器）
   - login: / Password: 交互登录（部分固件）
"""

from __future__ import annotations

import asyncio
import contextlib
import re

# ---------------------------------------------------------------- Telnet 协议
IAC = 0xFF
DONT = 0xFE
DO = 0xFD
WONT = 0xFC
WILL = 0xFB
SB = 0xFA
SE = 0xF0

_DATA, _IAC, _OPT, _SB, _SB_IAC = 0, 1, 2, 3, 4

START_MARKER = "@@XQLYQ_START@@"
END_MARKER = "@@XQLYQ_POLL_END@@"

# 去掉终端颜色/光标控制转义，避免污染数值
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]")


class TelnetError(Exception):
    """Telnet 相关错误基类。"""


class TelnetConnectionError(TelnetError):
    """连不上 / 中途断开 / 没拿到 shell。"""


class TelnetAuthError(TelnetError):
    """账号密码不对，或者需要密码但没配置。"""


class TelnetCommandError(TelnetError):
    """命令执行超时或返回不完整。"""


class _TelnetFilter:
    """剔除 Telnet 控制序列，并生成"拒绝一切选项"的回复。"""

    def __init__(self) -> None:
        self._state = _DATA
        self._opt = 0

    def feed(self, chunk: bytes) -> tuple[bytes, list[bytes]]:
        out = bytearray()
        replies: list[bytes] = []
        for byte in chunk:
            if self._state == _DATA:
                if byte == IAC:
                    self._state = _IAC
                else:
                    out.append(byte)
            elif self._state == _IAC:
                if byte == IAC:
                    out.append(IAC)
                    self._state = _DATA
                elif byte in (DO, DONT, WILL, WONT):
                    self._opt = byte
                    self._state = _OPT
                elif byte == SB:
                    self._state = _SB
                else:
                    self._state = _DATA
            elif self._state == _OPT:
                if self._opt == DO:
                    replies.append(bytes((IAC, WONT, byte)))
                elif self._opt == WILL:
                    replies.append(bytes((IAC, DONT, byte)))
                self._state = _DATA
            elif self._state == _SB:
                if byte == IAC:
                    self._state = _SB_IAC
            elif self._state == _SB_IAC:
                self._state = _DATA if byte == SE else _SB
        return bytes(out), replies


class _Session:
    """一次 Telnet 会话：读取、发送、按正则等待。"""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._filter = _TelnetFilter()

    async def send(self, data: str) -> None:
        self.writer.write(data.encode("utf-8", "ignore"))
        await self.writer.drain()

    async def read_until(self, patterns: list[re.Pattern[str]], timeout: float) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        text = ""
        while True:
            left = deadline - loop.time()
            if left <= 0:
                return text
            try:
                chunk = await asyncio.wait_for(self.reader.read(8192), left)
            except (asyncio.TimeoutError, TimeoutError):
                return text
            if not chunk:
                raise TelnetConnectionError("Telnet 连接已被对端关闭")
            clean, replies = self._filter.feed(chunk)
            for reply in replies:
                self.writer.write(reply)
            if replies:
                with contextlib.suppress(Exception):
                    await self.writer.drain()
            text += clean.decode("utf-8", "ignore")
            if any(p.search(text) for p in patterns):
                return text


# ------------------------------------------------------------------ 巡检脚本
# 全部用 busybox sh 兼容写法；每个指标一行 KEY=value，
# START / END 标记都用变量拼接，避免和输入回显冲突。
POLL_SCRIPT = r"""S=@@XQLYQ
P=@@XQLYQ
echo "${S}_START@@"
T=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -n1)
printf 'cputemp_raw=%s\n' "$T"
for z in /sys/class/thermal/thermal_zone*; do
tv=$(cat $z/temp 2>/dev/null)
ty=$(cat $z/type 2>/dev/null)
[ -n "$tv" ] || continue
printf 'zone=%s|%s\n' "${ty:-unknown}" "$tv"
done
printf 'loadavg=%s\n' "$(cat /proc/loadavg 2>/dev/null)"
printf 'stat=%s\n' "$(head -n 2 /proc/stat 2>/dev/null | tr '\n' ';')"
printf 'meminfo=%s\n' "$(tr '\n' ';' < /proc/meminfo 2>/dev/null)"
printf 'uptime=%s\n' "$(cat /proc/uptime 2>/dev/null)"
printf 'netdev=%s\n' "$(tr '\n' ';' < /proc/net/dev 2>/dev/null)"
printf 'arp=%s\n' "$(tr '\n' ';' < /proc/net/arp 2>/dev/null)"
printf 'dfroot=%s\n' "$(df -k / 2>/dev/null | tail -n1)"
printf 'cpufreq=%s\n' "$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null | head -n1)"
for h in /sys/class/hwmon/hwmon*; do
f=$(cat $h/fan1_input 2>/dev/null)
[ -n "$f" ] || continue
printf 'fan=%s|%s\n' "$(cat $h/name 2>/dev/null)" "$f"
done
MA=$(cat /proc/device-tree/model 2>/dev/null)
[ -n "$MA" ] || MA=$(cat /tmp/sysinfo/model 2>/dev/null)
printf 'model=%s\n' "$MA"
V=$(cat /etc/mi_version 2>/dev/null | head -n1)
[ -n "$V" ] || V=$(cat /etc/xiaoqiang/xq_version 2>/dev/null | head -n1)
[ -n "$V" ] || V=$(head -n1 /etc/openwrt_release 2>/dev/null)
printf 'version=%s\n' "$V"
printf 'hostname=%s\n' "$(uname -n 2>/dev/null)"
printf 'kernel=%s\n' "$(uname -r 2>/dev/null | head -c 64)"
printf 'procs=%s\n' "$(ps 2>/dev/null | wc -l)"
R=0
for w in $(ls /sys/class/net 2>/dev/null); do
case "$w" in
wl*|ra*|wlan*) R=$((R + 1)) ;;
esac
done
printf 'radios=%s\n' "$R"
ST=0
for w in $(ls /sys/class/net 2>/dev/null); do
case "$w" in
wl*|ra*|wlan*) n=$(iw dev "$w" station dump 2>/dev/null | grep -c '^Station'); ST=$((ST + ${n:-0})) ;;
esac
done
printf 'stations=%s\n' "$ST"
echo "${P}_POLL_END@@"
"""

# 通用命令模板（给 run_command 服务用）
EXEC_TEMPLATE = """S=@@XQLYQ
P=@@XQLYQ
echo "${S}_START@@"
__COMMAND__
echo "${P}_POLL_END@@"
"""

_LOGIN_RE = re.compile(r"login:\s*$", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"password:\s*$", re.IGNORECASE)
_PROMPT_RE = re.compile(r"(?:^|\n|[^\w])[#$>]\s*$")
_INCORRECT_RE = re.compile(r"(?:login|password)\s*incorrect|authentication\s*fail", re.IGNORECASE)


class XiaomiTelnetClient:
    """小米路由器 Telnet 客户端：一次调用 = 一次连接 + 一次执行。"""

    def __init__(
        self,
        host: str,
        port: int = 23,
        username: str = "root",
        password: str = "",
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout

    # ------------------------------------------------------------ 对外接口
    async def async_poll(self) -> str:
        """执行内置巡检脚本，返回清理后的 KEY=value 文本。"""
        return await self._async_run(POLL_SCRIPT, self.timeout + 8)

    async def async_execute(self, command: str) -> str:
        """执行任意一条 shell 命令（多行也行），返回清理后的输出。"""
        script = EXEC_TEMPLATE.replace("__COMMAND__", command.strip())
        return await self._async_run(script, self.timeout + 8)

    # ------------------------------------------------------------ 内部实现
    async def _async_run(self, script: str, timeout: float) -> str:
        session = await self._async_open()
        try:
            await session.send("\n")
            await session.send(script)
            if not script.endswith("\n"):
                await session.send("\n")
            text = await session.read_until([re.compile(re.escape(END_MARKER))], timeout)
            if END_MARKER not in text:
                raise TelnetCommandError(
                    "读取路由器数据超时：没收到结束标记（脚本被中断或 Telnet 服务异常）"
                )
            return self._extract(text)
        finally:
            await self._async_close(session)

    async def _async_open(self) -> _Session:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, int(self.port)), timeout=self.timeout
            )
        except (asyncio.TimeoutError, TimeoutError) as err:
            raise TelnetConnectionError(f"连接 {self.host}:{self.port} 超时") from err
        except OSError as err:
            raise TelnetConnectionError(f"连接 {self.host}:{self.port} 失败：{err}") from err

        session = _Session(reader, writer)
        try:
            await self._async_login(session)
        except TelnetError:
            await self._async_close(session)
            raise
        except Exception:
            await self._async_close(session)
            raise
        return session

    @staticmethod
    async def _async_close(session: _Session) -> None:
        with contextlib.suppress(Exception):
            session.writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(session.writer.wait_closed(), 3)

    async def _async_login(self, session: _Session) -> None:
        text = await session.read_until(
            [_LOGIN_RE, _PASSWORD_RE, _PROMPT_RE, _INCORRECT_RE], min(self.timeout, 8)
        )
        if _INCORRECT_RE.search(text):
            raise TelnetAuthError("路由器拒绝登录（用户名或密码错误）")

        if _LOGIN_RE.search(text):
            await session.send(self.username + "\n")
            text = await session.read_until(
                [_PASSWORD_RE, _PROMPT_RE, _INCORRECT_RE], min(self.timeout, 8)
            )
            if _INCORRECT_RE.search(text):
                raise TelnetAuthError("用户名不正确")
            if _PROMPT_RE.search(text) and not _PASSWORD_RE.search(text):
                # 某些固件账号对了直接给 shell
                pass

        if _PASSWORD_RE.search(text):
            # 密码允许为空：小米路由 telnetd 常见就是 root 空密码，
            # 此时「按一次回车」就能通过，所以填不填都要发一行过去。
            await session.send((self.password or "") + "\n")
            text = await session.read_until(
                [_PROMPT_RE, _INCORRECT_RE, _LOGIN_RE], min(self.timeout, 10)
            )
            if _INCORRECT_RE.search(text):
                raise TelnetAuthError("路由器 Telnet 密码不正确")
            if _LOGIN_RE.search(text):
                hint = "" if self.password else "（当前密码为空，若该机型需要 root 密码请补填）"
                raise TelnetAuthError(f"路由器又要求重新登录，密码可能不正确{hint}")

        if not _PROMPT_RE.search(text):
            # 个别固件要敲个回车才出提示符
            await session.send("\n")
            text = await session.read_until([_PROMPT_RE, _LOGIN_RE], 4)
            if _LOGIN_RE.search(text):
                raise TelnetAuthError("路由器仍然要求登录，请检查账号密码")
            if not _PROMPT_RE.search(text):
                raise TelnetConnectionError(
                    "已连接但没拿到 shell 提示符（Telnet 服务可能没有真正开启）"
                )

    @staticmethod
    def _extract(text: str) -> str:
        """从原始流里切出真正的命令输出，丢掉输入回显与标记本身。"""
        start = text.rfind(START_MARKER)
        if start == -1:
            return _ANSI_RE.sub("", text).replace("\r", "").strip("\n")
        body = text[start + len(START_MARKER) :]
        end = body.rfind(END_MARKER)
        if end != -1:
            body = body[:end]
        body = _ANSI_RE.sub("", body).replace("\r", "")
        return body.strip("\n")
