"""Switch platform for Stromzähler provider submissions."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StromzaehlerConfigEntry
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StromzaehlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    if manager.provider_supports_submission:
        async_add_entities([AutomaticMeterSubmissionSwitch(manager, entry)])


class AutomaticMeterSubmissionSwitch(SwitchEntity):
    """Enable automatic meter reading submissions."""

    _attr_has_entity_name = True
    _attr_translation_key = "automatic_submission"
    _attr_should_poll = False

    def __init__(self, manager, entry: StromzaehlerConfigEntry) -> None:
        self.manager = manager
        self._attr_unique_id = f"{entry.entry_id}_automatic_submission"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="DrLippe",
            model="Virtueller Stromzähler",
        )
        self._remove_listener = None

    @property
    def is_on(self) -> bool:
        return self.manager.auto_submission_enabled

    @property
    def extra_state_attributes(self):
        return {
            "grid_operator": self.manager.grid_operator,
            "submission_frequency": self.manager.submission_frequency,
            "current_submission_period": self.manager.submission_period_key(),
            "last_submission_attempt_period": self.manager.last_submission_attempt_period,
            "last_submission_attempt_date": self.manager.last_submission_attempt_date,
            "last_submission_date": self.manager.last_submission_date,
            "last_submission_value_kwh": self.manager.last_submission_value,
            "last_submission_status": self.manager.last_submission_status,
            "last_submission_error": self.manager.last_submission_error,
        }

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_auto_submission(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_auto_submission(False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._remove_listener = self.manager.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
