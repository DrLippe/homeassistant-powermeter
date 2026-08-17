"""Stromzähler integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .manager import StromzaehlerManager


type StromzaehlerConfigEntry = ConfigEntry[StromzaehlerManager]


async def async_setup_entry(hass: HomeAssistant, entry: StromzaehlerConfigEntry) -> bool:
    """Set up Stromzähler from a config entry."""
    manager = StromzaehlerManager(hass, entry)
    await manager.async_start()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StromzaehlerConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: StromzaehlerConfigEntry) -> None:
    """Reload the integration after options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)
