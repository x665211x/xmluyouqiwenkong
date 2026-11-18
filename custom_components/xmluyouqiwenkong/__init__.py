"""小米路由器温控/监控集成（domain: xmluyouqiwenkong）入口。

通过 Telnet 采集小米路由器（如 小米路由器 10000 Pro / AX 系列）的全部运行状态：
温度、CPU、内存、负载、存储、网卡流量、在线设备、Wi-Fi 终端数、风扇转速等。
"""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_TEMP_THRESHOLD, DOMAIN
from .coordinator import XiaomiRouterCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
]

# HA 2026 起 homeassistant.const 不再导出 ATTR_ENTRY_ID，自己定义这一个字符串常量即可
ATTR_ENTRY_ID = "entry_id"

SERVICE_PING = "ping"
SERVICE_TRACEROUTE = "traceroute"
SERVICE_TAIL_LOG = "tail_log"

# 只放行这些字符，避免服务参数被拼成额外的 shell 命令
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/\-]+$")


def _safe_token(value: Any, field: str) -> str:
    """校验服务参数：只允许字母、数字与 . : / _ -。"""
    text = str(value or "").strip()
    if not _SAFE_TOKEN.match(text):
        raise HomeAssistantError(
            f"{field} 取值不合法：只允许字母、数字和 . : / _ -，收到 {text!r}"
        )
    return text


SERVICE_PING_SCHEMA = vol.Schema(
    {
        vol.Optional("target", default="223.5.5.5"): str,
        vol.Optional("count", default=4): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=20)
        ),
        vol.Optional(ATTR_ENTRY_ID): str,
    }
)

SERVICE_TRACEROUTE_SCHEMA = vol.Schema(
    {
        vol.Optional("target", default="223.5.5.5"): str,
        vol.Optional("max_hops", default=12): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=30)
        ),
        vol.Optional(ATTR_ENTRY_ID): str,
    }
)

SERVICE_TAIL_LOG_SCHEMA = vol.Schema(
    {
        vol.Optional("path", default="/tmp/messages"): str,
        vol.Optional("lines", default=40): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=200)
        ),
        vol.Optional(ATTR_ENTRY_ID): str,
    }
)

SERVICE_RUN_COMMAND = "run_command"
SERVICE_RUN_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("command"): str,
        vol.Optional(ATTR_ENTRY_ID): str,
    }
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """注册服务：run_command（任意命令）、ping、traceroute、tail_log。"""

    def _resolve(call: ServiceCall) -> XiaomiRouterCoordinator:
        entry_id = call.data.get(ATTR_ENTRY_ID)
        coordinators: dict[str, XiaomiRouterCoordinator] = hass.data.get(DOMAIN, {})

        if entry_id:
            coordinator = coordinators.get(entry_id)
            if coordinator is None:
                raise HomeAssistantError(f"找不到指定的配置条目：{entry_id}")
            return coordinator
        if not coordinators:
            raise HomeAssistantError("还没有添加任何小米路由器")
        return next(iter(coordinators.values()))

    async def _async_run_command(call: ServiceCall) -> dict[str, Any]:
        output = await _resolve(call).async_run_command(call.data["command"])
        _LOGGER.debug("run_command 执行完成，输出 %s 字节", len(output))
        return {"output": output}

    async def _async_ping(call: ServiceCall) -> dict[str, Any]:
        target = _safe_token(call.data.get("target", "223.5.5.5"), "target")
        count = int(call.data.get("count", 4))
        return {
            "output": await _resolve(call).async_run_command(
                f"ping -c {count} {target} 2>&1"
            )
        }

    async def _async_traceroute(call: ServiceCall) -> dict[str, Any]:
        target = _safe_token(call.data.get("target", "223.5.5.5"), "target")
        max_hops = int(call.data.get("max_hops", 12))
        return {
            "output": await _resolve(call).async_run_command(
                f"traceroute -m {max_hops} {target} 2>&1 || traceroute {target} 2>&1"
            )
        }

    async def _async_tail_log(call: ServiceCall) -> dict[str, Any]:
        path = _safe_token(call.data.get("path", "/tmp/messages"), "path")
        lines = int(call.data.get("lines", 40))
        return {
            "output": await _resolve(call).async_run_command(
                f"tail -n {lines} {path} 2>&1"
            )
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_COMMAND,
        _async_run_command,
        schema=SERVICE_RUN_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PING,
        _async_ping,
        schema=SERVICE_PING_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TRACEROUTE,
        _async_traceroute,
        schema=SERVICE_TRACEROUTE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TAIL_LOG,
        _async_tail_log,
        schema=SERVICE_TAIL_LOG_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立配置条目时初始化协调器与实体。"""
    coordinator = XiaomiRouterCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置条目。"""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.async_close()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """IP / 端口 / 账号 / 扫描间隔等连接参数变化后重新加载。

    温度阈值只是运行时状态（由 number 实体管理），不会触发重载。
    """
    coordinator: XiaomiRouterCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        return

    if coordinator.matches(entry):
        # 只有温度阈值这类运行时参数变了，直接生效，不必重载
        merged = {**entry.data, **entry.options}
        coordinator.set_threshold(merged.get(CONF_TEMP_THRESHOLD))
        return

    _LOGGER.debug("连接参数变化，重新加载 %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
