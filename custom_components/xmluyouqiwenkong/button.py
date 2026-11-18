"""手动刷新按钮：立即通过 Telnet 拉一次全部状态。"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import XiaomiRouterCoordinator
from .sensor import device_info

REFRESH_BUTTON = ButtonEntityDescription(
    key="refresh",
    translation_key="refresh",
    icon="mdi:refresh",
    entity_category=EntityCategory.DIAGNOSTIC,
)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: XiaomiRouterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([XiaomiRefreshButton(coordinator, entry)])


class XiaomiRefreshButton(CoordinatorEntity[XiaomiRouterCoordinator], ButtonEntity):
    """按一下立即刷新所有实体。"""

    _attr_has_entity_name = True
    entity_description = REFRESH_BUTTON

    def __init__(
        self, coordinator: XiaomiRouterCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = device_info(entry, coordinator.data)

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
