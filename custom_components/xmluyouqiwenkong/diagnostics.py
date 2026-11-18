"""集成诊断：从「设置 → 设备与服务 → 集成 → 下载诊断信息」导出，自动脱敏密码。"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, DOMAIN
from .coordinator import XiaomiRouterCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """返回可供排查使用的状态快照（不含明文密码）。"""
    coordinator: XiaomiRouterCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )

    entry_data = dict(entry.data)
    if CONF_PASSWORD in entry_data:
        entry_data[CONF_PASSWORD] = "***" if entry_data[CONF_PASSWORD] else "(空)"

    data = dict(coordinator.data or {}) if coordinator else {}
    raw_output = data.pop("raw", None)

    return {
        "entry": {
            "title": entry.title,
            "data": entry_data,
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success if coordinator else None,
            "last_exception": (
                repr(coordinator.last_exception)
                if coordinator and coordinator.last_exception
                else None
            ),
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator and coordinator.update_interval
                else None
            ),
            "protocol": getattr(coordinator, "protocol", None),
            "threshold": coordinator.threshold if coordinator else None,
        },
        "parsed": data,
        "raw_output": raw_output,
    }
