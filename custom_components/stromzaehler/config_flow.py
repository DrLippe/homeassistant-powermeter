"""Config flow for Stromzähler."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.energy.data import async_get_manager
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BILLING_START_DAY,
    CONF_BILLING_START_MONTH,
    CONF_EXPORT_ENTITY,
    CONF_EXPORT_OFFSET,
    CONF_IMPORT_ENTITY,
    CONF_IMPORT_OFFSET,
    CONF_METER_READING,
    CONF_METER_READING_EXPORT,
    DEFAULT_BILLING_START_DAY,
    DEFAULT_BILLING_START_MONTH,
    DEFAULT_NAME,
    DOMAIN,
    UNIT_KWH,
    UNIT_WH,
)

SUPPORTED_UNITS = {UNIT_WH, UNIT_KWH}


def _energy_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _month_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=str(m), label=str(m)) for m in range(1, 13)],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _number(default: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=-100000000,
            max=100000000,
            step=0.001,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="kWh",
        )
    )


def _setup_schema(defaults: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_IMPORT_ENTITY, default=defaults.get(CONF_IMPORT_ENTITY)): _energy_selector(),
        vol.Optional(CONF_EXPORT_ENTITY, default=defaults.get(CONF_EXPORT_ENTITY)): _energy_selector(),
        vol.Required(CONF_METER_READING, default=defaults.get(CONF_METER_READING, 0.0)): _number(0),
        vol.Optional(CONF_METER_READING_EXPORT, default=defaults.get(CONF_METER_READING_EXPORT, 0.0)): _number(0),
        vol.Required(CONF_BILLING_START_DAY, default=defaults.get(CONF_BILLING_START_DAY, 1)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=31, step=1, mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_BILLING_START_MONTH, default=str(defaults.get(CONF_BILLING_START_MONTH, 1))): _month_selector(),
    }
    return vol.Schema(fields)


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_IMPORT_ENTITY, default=defaults.get(CONF_IMPORT_ENTITY)): _energy_selector(),
            vol.Optional(CONF_EXPORT_ENTITY, default=defaults.get(CONF_EXPORT_ENTITY)): _energy_selector(),
            vol.Required(CONF_IMPORT_OFFSET, default=float(defaults.get(CONF_IMPORT_OFFSET, 0.0))): _number(0),
            vol.Required(CONF_EXPORT_OFFSET, default=float(defaults.get(CONF_EXPORT_OFFSET, 0.0))): _number(0),
            vol.Required(CONF_BILLING_START_DAY, default=defaults.get(CONF_BILLING_START_DAY, 1)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=31, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_BILLING_START_MONTH, default=str(defaults.get(CONF_BILLING_START_MONTH, 1))): _month_selector(),
        }
    )


async def _dashboard_grid_entities(hass) -> dict[str, str]:
    """Return grid import/export entities configured in the Energy dashboard."""
    found: dict[str, str] = {}
    try:
        manager = await async_get_manager(hass)
        prefs = manager.data or {}
        for source in prefs.get("energy_sources", []):
            if source.get("type") != "grid":
                continue
            for pref_key, conf_key in (
                ("stat_energy_from", CONF_IMPORT_ENTITY),
                ("stat_energy_to", CONF_EXPORT_ENTITY),
            ):
                entity_id = source.get(pref_key)
                if isinstance(entity_id, str) and "." in entity_id and hass.states.get(entity_id):
                    found[conf_key] = entity_id
            break
    except (ImportError, AttributeError, TypeError):
        pass
    return found


def _entity_kwh(hass, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.attributes.get("unit_of_measurement") not in SUPPORTED_UNITS:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    return value / 1000.0 if state.attributes.get("unit_of_measurement") == UNIT_WH else value


def _validate_entity(hass, entity_id: str | None, required: bool) -> str | None:
    if not entity_id:
        return "source_not_found" if required else None
    state = hass.states.get(entity_id)
    if state is None:
        return "source_not_found"
    if state.attributes.get("unit_of_measurement") not in SUPPORTED_UNITS:
        return "unsupported_unit"
    return None


class StromzaehlerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stromzähler."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        defaults = dict(user_input or {})
        if user_input is None:
            defaults.update(await _dashboard_grid_entities(self.hass))

        if user_input is not None:
            user_input = dict(user_input)
            user_input[CONF_BILLING_START_DAY] = int(user_input[CONF_BILLING_START_DAY])
            user_input[CONF_BILLING_START_MONTH] = int(user_input[CONF_BILLING_START_MONTH])

            if error := _validate_entity(self.hass, user_input.get(CONF_IMPORT_ENTITY), True):
                errors[CONF_IMPORT_ENTITY] = error
            if error := _validate_entity(self.hass, user_input.get(CONF_EXPORT_ENTITY), False):
                errors[CONF_EXPORT_ENTITY] = error
            try:
                date(2000, user_input[CONF_BILLING_START_MONTH], user_input[CONF_BILLING_START_DAY])
            except ValueError:
                errors[CONF_BILLING_START_DAY] = "invalid_billing_date"

            import_source = _entity_kwh(self.hass, user_input.get(CONF_IMPORT_ENTITY))
            export_source = _entity_kwh(self.hass, user_input.get(CONF_EXPORT_ENTITY))
            if import_source is None:
                errors.setdefault(CONF_IMPORT_ENTITY, "invalid_source_value")
            if user_input.get(CONF_EXPORT_ENTITY) and export_source is None:
                errors.setdefault(CONF_EXPORT_ENTITY, "invalid_source_value")

            if not errors:
                user_input[CONF_IMPORT_OFFSET] = float(user_input[CONF_METER_READING]) - import_source
                user_input[CONF_EXPORT_OFFSET] = float(user_input.get(CONF_METER_READING_EXPORT, 0.0)) - (export_source or 0.0)
                await self.async_set_unique_id(user_input[CONF_IMPORT_ENTITY])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)

        return self.async_show_form(step_id="user", data_schema=_setup_schema(defaults), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> StromzaehlerOptionsFlow:
        return StromzaehlerOptionsFlow()


class StromzaehlerOptionsFlow(OptionsFlowWithReload):
    """Manage Stromzähler options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = dict(user_input)
            user_input[CONF_BILLING_START_DAY] = int(user_input[CONF_BILLING_START_DAY])
            user_input[CONF_BILLING_START_MONTH] = int(user_input[CONF_BILLING_START_MONTH])
            if error := _validate_entity(self.hass, user_input.get(CONF_IMPORT_ENTITY), True):
                errors[CONF_IMPORT_ENTITY] = error
            if error := _validate_entity(self.hass, user_input.get(CONF_EXPORT_ENTITY), False):
                errors[CONF_EXPORT_ENTITY] = error
            try:
                date(2000, user_input[CONF_BILLING_START_MONTH], user_input[CONF_BILLING_START_DAY])
            except ValueError:
                errors[CONF_BILLING_START_DAY] = "invalid_billing_date"
            if not errors:
                return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(user_input or current),
            errors=errors,
        )
