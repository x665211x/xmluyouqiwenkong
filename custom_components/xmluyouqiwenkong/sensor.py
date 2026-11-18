"""传感器实体：把协调器里的全部状态映射成 Home Assistant 实体。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfFrequency,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, DOMAIN
from .coordinator import XiaomiRouterCoordinator


def _get(key: str) -> Callable[[dict[str, Any]], Any]:
    """按字段名直接取值。"""

    def _value(data: dict[str, Any]) -> Any:
        return data.get(key)

    return _value


def _device_list(data: dict[str, Any]) -> dict[str, Any]:
    devices = data.get("devices") or []
    return {"online_devices": devices, "device_count": len(devices)}


def _cpu_temp_attrs(data: dict[str, Any]) -> dict[str, Any]:
    zones = data.get("thermal_zones") or {}
    return {
        "source_zone": data.get("cpu_temp_source"),
        "all_zones": {key: info.get("temperature") for key, info in zones.items()},
    }


def _fan_attrs(data: dict[str, Any]) -> dict[str, Any]:
    fans = data.get("fans") or []
    return {"fans": fans, "fan_name": data.get("fan_name")}


def _snapshot_attrs(data: dict[str, Any]) -> dict[str, Any]:
    attrs = {
        key: value
        for key, value in data.items()
        if key not in ("raw", "devices", "thermal_zones", "fans")
        and not isinstance(value, (list, dict))
        and value is not None
    }
    attrs["thermal_zones"] = {
        key: info.get("temperature")
        for key, info in (data.get("thermal_zones") or {}).items()
    }
    attrs["fans"] = data.get("fans") or []
    attrs["devices"] = data.get("devices") or []
    return attrs


def device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> DeviceInfo:
    """同一台路由器的所有实体共用一个设备卡片。"""
    data = data or {}
    host = entry.data.get(CONF_HOST) or entry.options.get(CONF_HOST) or ""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Xiaomi",
        name=f"小米路由器 {host}".strip(),
        model=data.get("model"),
        sw_version=data.get("firmware"),
        hw_version=data.get("kernel"),
        configuration_url=f"http://{host.split(':')[0]}" if host else None,
    )


@dataclass(frozen=True, kw_only=True)
class XmSensorEntityDescription(SensorEntityDescription):
    """在标准描述上挂两个取值函数。"""

    value_fn: Callable[[dict[str, Any]], Any] = field(default=lambda data: None)
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSOR_TYPES: tuple[XmSensorEntityDescription, ...] = (
    XmSensorEntityDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        value_fn=_get("cpu_temperature"),
        attributes_fn=_cpu_temp_attrs,
    ),
    XmSensorEntityDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:cpu-64-bit",
        value_fn=_get("cpu_usage"),
    ),
    XmSensorEntityDescription(
        key="cpu_frequency",
        translation_key="cpu_frequency",
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        value_fn=_get("cpu_frequency"),
    ),
    XmSensorEntityDescription(
        key="load_1m",
        translation_key="load_1m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:chart-line",
        value_fn=_get("load_1m"),
    ),
    XmSensorEntityDescription(
        key="load_5m",
        translation_key="load_5m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:chart-line",
        value_fn=_get("load_5m"),
    ),
    XmSensorEntityDescription(
        key="load_15m",
        translation_key="load_15m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:chart-line",
        value_fn=_get("load_15m"),
    ),
    XmSensorEntityDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:memory",
        value_fn=_get("memory_usage"),
    ),
    XmSensorEntityDescription(
        key="memory_available",
        translation_key="memory_available",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:memory",
        value_fn=_get("memory_available"),
    ),
    XmSensorEntityDescription(
        key="memory_total",
        translation_key="memory_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:memory",
        value_fn=_get("memory_total"),
    ),
    XmSensorEntityDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        icon="mdi:clock-outline",
        value_fn=_get("uptime"),
    ),
    XmSensorEntityDescription(
        key="connected_devices",
        translation_key="connected_devices",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:devices",
        value_fn=_get("connected_devices"),
        attributes_fn=_device_list,
    ),
    XmSensorEntityDescription(
        key="wifi_clients",
        translation_key="wifi_clients",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi",
        value_fn=_get("wifi_clients"),
    ),
    XmSensorEntityDescription(
        key="rx_rate",
        translation_key="rx_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:download-network",
        value_fn=_get("rx_rate"),
    ),
    XmSensorEntityDescription(
        key="tx_rate",
        translation_key="tx_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:upload-network",
        value_fn=_get("tx_rate"),
    ),
    XmSensorEntityDescription(
        key="rx_total",
        translation_key="rx_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        icon="mdi:download",
        value_fn=_get("rx_total"),
    ),
    XmSensorEntityDescription(
        key="tx_total",
        translation_key="tx_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        icon="mdi:upload",
        value_fn=_get("tx_total"),
    ),
    XmSensorEntityDescription(
        key="storage_usage",
        translation_key="storage_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=_get("storage_usage"),
    ),
    XmSensorEntityDescription(
        key="storage_used",
        translation_key="storage_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=_get("storage_used"),
    ),
    XmSensorEntityDescription(
        key="storage_total",
        translation_key="storage_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=_get("storage_total"),
    ),
    XmSensorEntityDescription(
        key="fan_speed",
        translation_key="fan_speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:fan",
        value_fn=_get("fan_speed"),
        attributes_fn=_fan_attrs,
    ),
    XmSensorEntityDescription(
        key="processes",
        translation_key="processes",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:format-list-numbered",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_get("processes"),
    ),
    XmSensorEntityDescription(
        key="wifi_radios",
        translation_key="wifi_radios",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi-strength-4",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_get("wifi_radios"),
    ),
    XmSensorEntityDescription(
        key="model",
        translation_key="model",
        icon="mdi:router-wireless",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_get("model"),
    ),
    XmSensorEntityDescription(
        key="firmware",
        translation_key="firmware",
        icon="mdi:package-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_get("firmware"),
    ),
    XmSensorEntityDescription(
        key="kernel",
        translation_key="kernel",
        icon="mdi:linux",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_get("kernel"),
    ),
    XmSensorEntityDescription(
        key="hostname",
        translation_key="hostname",
        icon="mdi:tag",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_get("hostname"),
    ),
    XmSensorEntityDescription(
        key="snapshot",
        translation_key="snapshot",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: "在线" if data.get("raw") else "无数据",
        attributes_fn=_snapshot_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: XiaomiRouterCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        XiaomiRouterSensor(coordinator, entry, description) for description in SENSOR_TYPES
    )

    known_zones: set[str] = set((coordinator.data or {}).get("thermal_zones") or {})
    if known_zones:
        async_add_entities(
            (XiaomiThermalZoneSensor(coordinator, entry, zone) for zone in known_zones),
            update_before_add=True,
        )

    @callback
    def _async_add_new_zones() -> None:
        """路由器上电后可能出现新的温区（例如无线芯片），动态补建实体。"""
        zones = set((coordinator.data or {}).get("thermal_zones") or {})
        new_zones = zones - known_zones
        if not new_zones:
            return
        known_zones.update(new_zones)
        async_add_entities(
            (XiaomiThermalZoneSensor(coordinator, entry, zone) for zone in new_zones),
            update_before_add=True,
        )

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_zones))


class XiaomiRouterSensor(CoordinatorEntity[XiaomiRouterCoordinator], SensorEntity):
    """一个状态一个实体。"""

    _attr_has_entity_name = True
    entity_description: XmSensorEntityDescription

    def __init__(
        self,
        coordinator: XiaomiRouterCoordinator,
        entry: ConfigEntry,
        description: XmSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry, coordinator.data)

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.attributes_fn or not self.coordinator.data:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)


class XiaomiThermalZoneSensor(CoordinatorEntity[XiaomiRouterCoordinator], SensorEntity):
    """单个温区（thermal_zone）的温度。"""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:thermometer-lines"

    def __init__(
        self,
        coordinator: XiaomiRouterCoordinator,
        entry: ConfigEntry,
        zone_key: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_key = zone_key
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_key}"
        self._attr_name = f"温区 {zone_key}"
        self._attr_device_info = device_info(entry, coordinator.data)

    @property
    def _zone(self) -> dict[str, Any] | None:
        zones = (self.coordinator.data or {}).get("thermal_zones") or {}
        return zones.get(self.zone_key)

    @property
    def available(self) -> bool:
        return super().available and self._zone is not None

    @property
    def native_value(self) -> Any:
        zone = self._zone
        return zone.get("temperature") if zone else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        zone = self._zone
        if not zone:
            return None
        return {"zone_type": zone.get("type"), "zone_key": self.zone_key}
