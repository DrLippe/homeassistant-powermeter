"""Config flow for Stromzähler."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BILLING_START_DAY,
    CONF_BILLING_START_MONTH,
    CONF_METER_READING,
    CONF_METER_READING_EXPORT,
    CONF_SOURCE_ENTITY,
    DEFAULT_BILLING_START_DAY,
    DEFAULT_BILLING_START_MONTH,
    DEFAULT_NAME,
    DOMAIN,
    UNIT_KW,
    UNIT_KWH,
    UNIT_W,
    UNIT_WH,
)

SUPPORTED_UNITS = {UNIT_W, UNIT_KW, UNIT_WH, UNIT_KWH}


def _source_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _month_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=str(month), label=str(month))
                for month in range(1, 13)
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _setup_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_SOURCE_ENTITY,
                default=defaults.get(CONF_SOURCE_ENTITY),
            ): _source_selector(),
            vol.Required(
                CONF_METER_READING,
                default=defaults.get(CONF_METER_READING, 0.0),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    step=0.001,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="kWh",
                )
            ),
            vol.Optional(
                CONF_METER_READING_EXPORT,
                default=defaults.get(CONF_METER_READING_EXPORT, 0.0),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    step=0.001,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="kWh",
                )
            ),
            vol.Required(
                CONF_BILLING_START_DAY,
                default=defaults.get(
                    CONF_BILLING_START_DAY, DEFAULT_BILLING_START_DAY
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=31,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_BILLING_START_MONTH,
                default=str(
                    defaults.get(
                        CONF_BILLING_START_MONTH, DEFAULT_BILLING_START_MONTH
                    )
                ),
            ): _month_selector(),
        }
    )


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SOURCE_ENTITY,
                default=defaults[CONF_SOURCE_ENTITY],
            ): _source_selector(),
            vol.Required(
                CONF_BILLING_START_DAY,
                default=defaults.get(
                    CONF_BILLING_START_DAY, DEFAULT_BILLING_START_DAY
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=31,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_BILLING_START_MONTH,
                default=str(
                    defaults.get(
                        CONF_BILLING_START_MONTH, DEFAULT_BILLING_START_MONTH
                    )
                ),
            ): _month_selector(),
        }
    )


class StromzaehlerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stromzähler."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up Stromzähler."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = dict(user_input)
            user_input[CONF_BILLING_START_DAY] = int(
                user_input[CONF_BILLING_START_DAY]
            )
            user_input[CONF_BILLING_START_MONTH] = int(
                user_input[CONF_BILLING_START_MONTH]
            )

            errors.update(self._validate_input(user_input))
            if not errors:
                await self.async_set_unique_id(user_input[CONF_SOURCE_ENTITY])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_setup_schema(user_input),
            errors=errors,
        )

    def _validate_input(self, user_input: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        source = self.hass.states.get(user_input[CONF_SOURCE_ENTITY])
        if source is None:
            errors[CONF_SOURCE_ENTITY] = "source_not_found"
        else:
            unit = source.attributes.get("unit_of_measurement")
            if unit not in SUPPORTED_UNITS:
                errors[CONF_SOURCE_ENTITY] = "unsupported_unit"

        try:
            date(
                2000,
                int(user_input[CONF_BILLING_START_MONTH]),
                int(user_input[CONF_BILLING_START_DAY]),
            )
        except ValueError:
            errors[CONF_BILLING_START_DAY] = "invalid_billing_date"
        return errors

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> StromzaehlerOptionsFlow:
        """Create the options flow."""
        return StromzaehlerOptionsFlow()


class StromzaehlerOptionsFlow(OptionsFlowWithReload):
    """Manage Stromzähler options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = dict(user_input)
            user_input[CONF_BILLING_START_DAY] = int(
                user_input[CONF_BILLING_START_DAY]
            )
            user_input[CONF_BILLING_START_MONTH] = int(
                user_input[CONF_BILLING_START_MONTH]
            )

            source = self.hass.states.get(user_input[CONF_SOURCE_ENTITY])
            if source is None:
                errors[CONF_SOURCE_ENTITY] = "source_not_found"
            elif source.attributes.get("unit_of_measurement") not in SUPPORTED_UNITS:
                errors[CONF_SOURCE_ENTITY] = "unsupported_unit"

            try:
                date(
                    2000,
                    user_input[CONF_BILLING_START_MONTH],
                    user_input[CONF_BILLING_START_DAY],
                )
            except ValueError:
                errors[CONF_BILLING_START_DAY] = "invalid_billing_date"

            if not errors:
                return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(user_input or current),
            errors=errors,
        )
