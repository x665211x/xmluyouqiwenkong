"""SSH 传输层：用 paramiko 在路由器上跑同一套巡检脚本。

为什么单独一个模块：
    Telnet 是纯标准库实现的；SSH 没法用标准库做，必须依赖 paramiko。
    所以 paramiko 只在真正执行时才 import —— 只用 Telnet 的用户不会因为
    SSH 相关的问题影响集成加载。

复用 telnet_client 里的 POLL_SCRIPT / EXEC_TEMPLATE / 输出清洗逻辑，
这样两种传输方式拿到的是同一份 KEY=value 文本，解析层完全不用改。
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .telnet_client import (
    EXEC_TEMPLATE,
    POLL_SCRIPT,
    TelnetAuthError,
    TelnetCommandError,
    TelnetConnectionError,
    XiaomiTelnetClient,
)

_LOGGER = logging.getLogger(__name__)


class XiaomiSshClient:
    """小米路由器 SSH 客户端（dropbear，一般是 root + 密码，端口 22）。

    对外接口和 XiaomiTelnetClient 一致：async_poll / async_execute。
    paramiko 是阻塞库，所以放到 HA 的执行器线程里跑，绝不占用事件循环。
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int = 22,
        username: str = "root",
        password: str = "",
        timeout: float = 10.0,
    ) -> None:
        self._hass = hass
        self.host = host
        self.port = int(port)
        self.username = username or "root"
        self.password = password or ""
        self.timeout = float(timeout)

    # ------------------------------------------------------------ 对外接口
    async def async_poll(self) -> str:
        """执行内置巡检脚本，返回清理后的 KEY=value 文本。"""
        return await self._hass.async_add_executor_job(
            self._run_sync, POLL_SCRIPT, self.timeout + 8
        )

    async def async_execute(self, command: str) -> str:
        """执行任意一条 shell 命令（多行也行），返回清理后的输出。"""
        script = EXEC_TEMPLATE.replace("__COMMAND__", command.strip())
        return await self._hass.async_add_executor_job(
            self._run_sync, script, self.timeout + 8
        )

    # ------------------------------------------------------------ 内部实现
    def _run_sync(self, script: str, timeout: float) -> str:
        try:
            import paramiko
        except ImportError as err:  # pragma: no cover - 正常安装不会走到这里
            raise TelnetConnectionError(
                "SSH 需要 paramiko，但当前环境没有安装；"
                "请确认 Home Assistant 能联网并允许安装 requirements"
            ) from err

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password or None,
                timeout=self.timeout,
                auth_timeout=self.timeout,
                banner_timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except paramiko.AuthenticationException as err:
            raise TelnetAuthError(f"SSH 认证失败：{err}") from err
        except (paramiko.SSHException, OSError) as err:
            # 没填密码时 paramiko 只会拿到 "No authentication methods available"，本质也是认证问题
            if "auth" in str(err).lower() or "password" in str(err).lower():
                raise TelnetAuthError(
                    f"SSH 认证失败（密码留空或不被接受）：{err}"
                ) from err
            raise TelnetConnectionError(f"连接 {self.host}:{self.port} 失败：{err}") from err

        try:
            _stdin, stdout, stderr = client.exec_command(script, timeout=timeout)
            out = stdout.read().decode("utf-8", "ignore")
            err_text = stderr.read().decode("utf-8", "ignore")
        except Exception as err:  # noqa: BLE001 - paramiko 抛的类型比较杂
            raise TelnetConnectionError(f"SSH 执行命令失败：{err}") from err
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

        from .telnet_client import END_MARKER  # 局部导入，避免循环依赖

        if END_MARKER not in out:
            detail = err_text.strip()[:200]
            raise TelnetCommandError(
                "SSH 没拿到巡检结果（脚本被中断或账号没有执行权限）"
                + (f"：{detail}" if detail else "")
            )
        return XiaomiTelnetClient._extract(out)
