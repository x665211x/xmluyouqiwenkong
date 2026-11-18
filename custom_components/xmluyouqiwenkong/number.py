"""温控阈值：可调数字实体，超过阈值时「过热告警」二进制传感器变为 on。"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_TEMP_THRESHOLD,
    DOMAIN,
    MAX_TEMP_THRESHOLD,
    MIN_TEMP_THRESHOLD,
)
from .coordinator import XiaomiRouterCoordinator
from .sensor import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: XiaomiRouterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([XiaomiTemperatureThreshold(coordinator, entry)])


class XiaomiTemperatureThreshold(CoordinatorEntity[XiaomiRouterCoordinator], NumberEntity):
    """温度告警阈值（℃）。值保存在配置条目的 options 里，重启后保持。"""

    _attr_has_entity_name = True
    _attr_translation_key = "temp_threshold"
    _attr_native_min_value = MIN_TEMP_THRESHOLD
    _attr_native_max_value = MAX_TEMP_THRESHOLD
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-alert"

    def __init__(
        self, coordinator: XiaomiRouterCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_temp_threshold"
        self._attr_device_info = device_info(entry, coordinator.data)

    @property
    def native_value(self) -> float:
        return self.coordinator.threshold

    async def async_set_native_value(self, value: float) -> None:
        options = dict(self.entry.options)
        options[CONF_TEMP_THRESHOLD] = float(value)
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        # 立即生效；options 监听器还会再应用一次，幂等
        self.coordinator.set_threshold(value)
        self.async_write_ha_state()
