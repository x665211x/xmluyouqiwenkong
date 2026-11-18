"""二进制传感器：在线状态、过热告警、风扇运转。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import XiaomiRouterCoordinator
from .sensor import device_info

ONLINE = BinarySensorEntityDescription(
    key="online",
    translation_key="online",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    icon="mdi:lan-connect",
)
OVERHEAT = BinarySensorEntityDescription(
    key="overheat",
    translation_key="overheat",
    device_class=BinarySensorDeviceClass.PROBLEM,
    icon="mdi:thermometer-alert",
)
FAN_RUNNING = BinarySensorEntityDescription(
    key="fan_running",
    translation_key="fan_running",
    icon="mdi:fan",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: XiaomiRouterCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        XiaomiRouterBinarySensor(coordinator, entry, ONLINE),
        XiaomiRouterBinarySensor(coordinator, entry, OVERHEAT),
    ]
    if (coordinator.data or {}).get("fan_speed") is not None:
        entities.append(XiaomiRouterBinarySensor(coordinator, entry, FAN_RUNNING))
    async_add_entities(entities)


class XiaomiRouterBinarySensor(CoordinatorEntity[XiaomiRouterCoordinator], BinarySensorEntity):
    """按 description.key 分派的三种二进制状态。"""

    _attr_has_entity_name = True
    entity_description: BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: XiaomiRouterCoordinator,
        entry: ConfigEntry,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry, coordinator.data)

    @property
    def available(self) -> bool:
        if self.entity_description.key == "online":
            return True
        return super().available

    @property
    def is_on(self) -> bool | None:
        key = self.entity_description.key
        data = self.coordinator.data or {}

        if key == "online":
            return self.coordinator.last_update_success

        if key == "overheat":
            temperature = data.get("cpu_temperature")
            if temperature is None:
                return None
            return float(temperature) >= float(self.coordinator.threshold)

        if key == "fan_running":
            rpm = data.get("fan_speed")
            if rpm is None:
                return None
            return int(rpm) > 0

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "overheat":
            return None
        data = self.coordinator.data or {}
        return {
            "threshold": self.coordinator.threshold,
            "cpu_temperature": data.get("cpu_temperature"),
            "source_zone": data.get("cpu_temp_source"),
        }
