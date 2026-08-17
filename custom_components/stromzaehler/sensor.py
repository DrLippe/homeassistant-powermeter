"""Sensor platform for Stromzähler."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import StromzaehlerConfigEntry
from .const import (
    ATTR_FLOW,
    ATTR_PERIOD_END,
    ATTR_PERIOD_START,
    ATTR_SOURCE_ENTITY,
    DOMAIN,
    FLOW_EXPORT,
    FLOW_IMPORT,
)
from .manager import StromzaehlerManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StromzaehlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stromzähler sensors."""
    manager = entry.runtime_data
    entities: list[SensorEntity] = []

    for flow in (FLOW_IMPORT, FLOW_EXPORT):
        entities.append(StromzaehlerMeterSensor(manager, entry, flow))
        for period in ("day", "month", "year"):
            entities.append(StromzaehlerPeriodSensor(manager, entry, flow, period))
            entities.append(StromzaehlerAverageSensor(manager, entry, flow, period))

    async_add_entities(entities)


class StromzaehlerBaseSensor(SensorEntity):
    """Base class for Stromzähler sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, manager: StromzaehlerManager, entry: StromzaehlerConfigEntry, suffix: str
    ) -> None:
        """Initialize a sensor."""
        self.manager = manager
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="DrLippe",
            model="Virtueller Stromzähler",
            entry_type=None,
        )
        self._remove_listener = None

    async def async_added_to_hass(self) -> None:
        """Register manager listener."""
        await super().async_added_to_hass()
        self._remove_listener = self.manager.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove manager listener."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class StromzaehlerMeterSensor(StromzaehlerBaseSensor):
    """Virtual physical meter register."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(
        self, manager: StromzaehlerManager, entry: StromzaehlerConfigEntry, flow: str
    ) -> None:
        super().__init__(manager, entry, f"meter_{flow}")
        self.flow = flow
        self._attr_translation_key = f"meter_{flow}"

    @property
    def native_value(self) -> float:
        """Return the current virtual meter reading."""
        return round(self.manager.meter_value(self.flow), 6)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {
            ATTR_FLOW: self.flow,
            ATTR_SOURCE_ENTITY: self.manager.source_entity,
        }


class StromzaehlerPeriodSensor(StromzaehlerBaseSensor):
    """Energy consumed/exported in an active period."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        manager: StromzaehlerManager,
        entry: StromzaehlerConfigEntry,
        flow: str,
        period: str,
    ) -> None:
        super().__init__(manager, entry, f"{flow}_{period}")
        self.flow = flow
        self.period = period
        self._attr_translation_key = f"{flow}_{period}"

    @property
    def native_value(self) -> float:
        return round(self.manager.period_value(self.flow, self.period), 6)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        now = dt_util.now()
        start = self.manager.period_start(self.period, now)
        if self.period == "day":
            end = start + timedelta(days=1)
        elif self.period == "month":
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
        else:
            end = self.manager.billing_year_end(now)
        return {
            ATTR_FLOW: self.flow,
            ATTR_PERIOD_START: start.isoformat(),
            ATTR_PERIOD_END: end.isoformat(),
        }


class StromzaehlerAverageSensor(StromzaehlerBaseSensor):
    """Average energy use over elapsed sub-periods."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        manager: StromzaehlerManager,
        entry: StromzaehlerConfigEntry,
        flow: str,
        period: str,
    ) -> None:
        super().__init__(manager, entry, f"{flow}_{period}_average")
        self.flow = flow
        self.period = period
        self._attr_translation_key = f"{flow}_{period}_average"
        self._attr_native_unit_of_measurement = {
            "day": "kWh/h",
            "month": "kWh/day",
            "year": "kWh/month",
        }[period]

    @property
    def native_value(self) -> float:
        return round(self.manager.average(self.flow, self.period), 6)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            ATTR_FLOW: self.flow,
            ATTR_PERIOD_START: self.manager.period_start(self.period).isoformat(),
        }
