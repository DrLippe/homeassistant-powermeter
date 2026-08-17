"""Stromzähler integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENERGY_SUPPLIER,
    CONF_EXPORT_OFFSET,
    CONF_GRID_OPERATOR,
    CONF_IMPORT_ENTITY,
    CONF_IMPORT_OFFSET,
    CONF_METER_READING,
    CONF_METER_READING_EXPORT,
    CONF_SOURCE_ENTITY,
    PLATFORMS,
    PROVIDER_NONE,
    UNIT_WH,
)
from .manager import StromzaehlerManager

_LOGGER = logging.getLogger(__name__)

type StromzaehlerConfigEntry = ConfigEntry[StromzaehlerManager]


async def async_setup_entry(hass: HomeAssistant, entry: StromzaehlerConfigEntry) -> bool:
    """Set up Stromzähler from a config entry."""
    manager = StromzaehlerManager(hass, entry)
    await manager.async_start()
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StromzaehlerConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older Stromzähler config entries."""
    data = dict(entry.data)

    if entry.version < 2:
        source_entity = data.get(CONF_SOURCE_ENTITY)
        state = hass.states.get(source_entity) if source_entity else None
        if state is None or state.attributes.get("unit_of_measurement") not in ("Wh", "kWh"):
            _LOGGER.error(
                "Cannot automatically migrate Stromzähler entry %s because the old source is not a cumulative energy entity; reconfigure the integration",
                entry.entry_id,
            )
            return False
        try:
            source_value = float(state.state)
        except (TypeError, ValueError):
            return False
        if state.attributes.get("unit_of_measurement") == UNIT_WH:
            source_value /= 1000.0
        data[CONF_IMPORT_ENTITY] = source_entity
        data[CONF_IMPORT_OFFSET] = float(data.get(CONF_METER_READING, 0.0)) - source_value
        data[CONF_EXPORT_OFFSET] = float(data.get(CONF_METER_READING_EXPORT, 0.0))

    if entry.version < 3:
        data.setdefault(CONF_GRID_OPERATOR, PROVIDER_NONE)
        data.setdefault(CONF_ENERGY_SUPPLIER, PROVIDER_NONE)

    hass.config_entries.async_update_entry(entry, data=data, version=3)
    return True
