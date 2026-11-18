"""数据协调器：按固定间隔用 Telnet 拉一次路由器全部状态。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_SCAN_INTERVAL,
    CONF_TEMP_THRESHOLD,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONN_KEYS,
    DEFAULT_PASSWORD,
    DEFAULT_PROTOCOL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMP_THRESHOLD,
    DEFAULT_TIMEOUT,
    DEFAULT_USERNAME,
    DEFAULT_SSH_PORT,
    DOMAIN,
    PROTOCOL_SSH,
)
from .parser import parse_poll
from .ssh_client import XiaomiSshClient
from .telnet_client import (
    TelnetAuthError,
    TelnetCommandError,
    TelnetConnectionError,
    TelnetError,
    XiaomiTelnetClient,
)

_LOGGER = logging.getLogger(__name__)


class XiaomiRouterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """负责 Telnet 取数 + 解析 + 缓存上一次累计量用于算速率。"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        merged: dict[str, Any] = {**entry.data, **entry.options}
        self.entry = entry
        self.protocol = str(merged.get(CONF_PROTOCOL) or DEFAULT_PROTOCOL).lower()
        host = str(merged.get(CONF_HOST, ""))
        port = int(merged.get(CONF_PORT) or DEFAULT_PORT)
        username = str(merged.get(CONF_USERNAME) or DEFAULT_USERNAME)
        password = str(merged.get(CONF_PASSWORD) or "")
        timeout = float(merged.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))

        if self.protocol == PROTOCOL_SSH:
            # SSH 走 paramiko（放到执行器线程里跑），端口没单独填就按 22
            self.client: XiaomiTelnetClient | XiaomiSshClient = XiaomiSshClient(
                hass,
                host=host,
                port=port if port != DEFAULT_PORT else DEFAULT_SSH_PORT,
                username=username,
                password=password,
                timeout=timeout,
            )
        else:
            self.client = XiaomiTelnetClient(
                host=host,
                port=port,
                username=username,
                password=password,
                timeout=timeout,
            )
        self._prev_counters: dict[str, Any] | None = None
        self._io_lock = asyncio.Lock()
        self.config_snapshot = self._snapshot(entry)
        self.threshold = float(merged.get(CONF_TEMP_THRESHOLD, DEFAULT_TEMP_THRESHOLD))

        # HA 2026 的 async_config_entry_first_refresh 会在 config_entry 为空时抛
        # ConfigEntryError，所以真正的配置条目必须显式传进去；配置向导里的
        # 探针用的是临时假对象，不能传。
        init_kwargs: dict[str, Any] = {}
        if isinstance(entry, ConfigEntry):
            init_kwargs["config_entry"] = entry

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.client.host}",
            update_interval=timedelta(
                seconds=int(merged.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
            ),
            **init_kwargs,
        )

    # ------------------------------------------------------------ 配置比对
    @staticmethod
    def _snapshot(entry: ConfigEntry) -> dict[str, str]:
        merged: dict[str, Any] = {**entry.data, **entry.options}
        return {key: str(merged.get(key)) for key in CONN_KEYS}

    def matches(self, entry: ConfigEntry) -> bool:
        """配置条目里的连接参数是否和当前一致（不一致才需要重载）。"""
        return self._snapshot(entry) == self.config_snapshot

    # ------------------------------------------------------------ 温度阈值
    def set_threshold(self, value: float | None) -> None:
        self.threshold = (
            float(value) if value is not None else DEFAULT_TEMP_THRESHOLD
        )
        self.async_update_listeners()

    # ------------------------------------------------------------ 取数
    async def _async_update_data(self) -> dict[str, Any]:
        async with self._io_lock:
            try:
                raw = await self.client.async_poll()
            except TelnetAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (TelnetConnectionError, TelnetCommandError) as err:
                raise UpdateFailed(str(err)) from err
            except OSError as err:
                raise UpdateFailed(f"网络错误：{err}") from err

            values, counters = parse_poll(raw, self._prev_counters, time.monotonic())
            self._prev_counters = counters
            values["raw"] = raw
            values["protocol"] = self.protocol
            values["scan_interval"] = self.update_interval.total_seconds() if self.update_interval else 0
            return values

    async def async_probe(self) -> dict[str, Any]:
        """配置向导用：验证一次连接并返回解析结果（不进缓存）。"""
        raw = await self.client.async_poll()
        values, _ = parse_poll(raw, None, time.monotonic())
        return values

    async def async_run_command(self, command: str) -> str:
        """执行任意 shell 命令（run_command 服务用）。"""
        async with self._io_lock:
            try:
                return await self.client.async_execute(command)
            except TelnetAuthError as err:
                raise HomeAssistantError(f"路由器认证失败：{err}") from err
            except TelnetError as err:
                raise HomeAssistantError(f"执行命令失败：{err}") from err

    async def async_close(self) -> None:
        """释放资源（当前实现每次轮询都断开连接，这里留作扩展）。"""
        return
