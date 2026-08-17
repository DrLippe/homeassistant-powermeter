"""Measurement, persistence and provider submission manager for Stromzähler."""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BILLING_START_DAY,
    CONF_BILLING_START_MONTH,
    CONF_CONTRACT_ACCOUNT,
    CONF_EXPORT_ENTITY,
    CONF_EXPORT_OFFSET,
    CONF_GRID_OPERATOR,
    CONF_IMPORT_ENTITY,
    CONF_IMPORT_OFFSET,
    CONF_METER_NUMBER,
    DEFAULT_BILLING_START_DAY,
    DEFAULT_BILLING_START_MONTH,
    FLOW_EXPORT,
    FLOW_IMPORT,
    PROVIDER_EAM_NETZ,
    PROVIDER_NONE,
    STORE_KEY_PREFIX,
    STORE_VERSION,
    SUBMISSION_INTERVAL,
    UNIT_WH,
    UPDATE_INTERVAL,
)
from .providers.base import MeterReadingSubmission
from .providers.eam_netz import EAMNetzError, EAMNetzProvider

_LOGGER = logging.getLogger(__name__)


class StromzaehlerManager:
    """Track cumulative import/export entities and provider submissions."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}
        self.import_entity = cfg[CONF_IMPORT_ENTITY]
        self.export_entity = cfg.get(CONF_EXPORT_ENTITY)
        self.import_offset = float(cfg.get(CONF_IMPORT_OFFSET, 0.0))
        self.export_offset = float(cfg.get(CONF_EXPORT_OFFSET, 0.0))
        self.billing_start_day = int(cfg.get(CONF_BILLING_START_DAY, DEFAULT_BILLING_START_DAY))
        self.billing_start_month = int(cfg.get(CONF_BILLING_START_MONTH, DEFAULT_BILLING_START_MONTH))
        self.grid_operator = str(cfg.get(CONF_GRID_OPERATOR, PROVIDER_NONE))
        self.contract_account = str(cfg.get(CONF_CONTRACT_ACCOUNT, "")).strip()
        self.meter_number = str(cfg.get(CONF_METER_NUMBER, "")).strip()

        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{STORE_KEY_PREFIX}.{entry.entry_id}"
        )
        self._unsubs: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []
        self._last_source: dict[str, float | None] = {FLOW_IMPORT: None, FLOW_EXPORT: None}
        self._last_meter: dict[str, float] = {FLOW_IMPORT: 0.0, FLOW_EXPORT: 0.0}
        self.day_import = self.day_export = 0.0
        self.month_import = self.month_export = 0.0
        self.year_import = self.year_export = 0.0
        self._day_key = self._month_key = self._year_key = ""

        self.auto_submission_enabled = False
        self.last_submission_date: str | None = None
        self.last_submission_value: float | None = None
        self.last_submission_status = "never"
        self.last_submission_error: str | None = None
        self._submission_running = False

        self._provider = None
        if (
            self.grid_operator == PROVIDER_EAM_NETZ
            and self.contract_account
            and self.meter_number
        ):
            self._provider = EAMNetzProvider(
                async_get_clientsession(hass),
                self.contract_account,
                self.meter_number,
            )

    async def async_start(self) -> None:
        await self._async_restore()
        now = dt_util.now()
        self._roll_periods(now)
        for flow in (FLOW_IMPORT, FLOW_EXPORT):
            value = self._read_source(flow)
            if value is not None:
                self._last_source[flow] = value
                self._last_meter[flow] = value + self.offset(flow)

        entities = [self.import_entity]
        if self.export_entity:
            entities.append(self.export_entity)
        self._unsubs.append(async_track_state_change_event(self.hass, entities, self._async_source_changed))
        self._unsubs.append(async_track_time_interval(self.hass, self._async_interval, UPDATE_INTERVAL))
        self._unsubs.append(async_track_time_interval(self.hass, self._async_submission_interval, SUBMISSION_INTERVAL))
        await self._async_save()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._async_save()

    @property
    def provider_supports_submission(self) -> bool:
        return self._provider is not None

    async def async_set_auto_submission(self, enabled: bool) -> None:
        """Enable or disable automatic provider submissions."""
        self.auto_submission_enabled = enabled
        await self._async_save()
        self._notify()
        if enabled:
            await self.async_try_submission()

    async def _async_submission_interval(self, now: datetime) -> None:
        if self.auto_submission_enabled:
            await self.async_try_submission(now)

    async def async_try_submission(self, now: datetime | None = None) -> bool:
        """Submit the current meter reading once per local calendar day."""
        if not self._provider or self._submission_running:
            return False

        local_now = dt_util.as_local(now or dt_util.now())
        today = local_now.date().isoformat()
        if self.last_submission_date == today and self.last_submission_status == "success":
            return True

        reading_value = self.meter_value(FLOW_IMPORT)
        self._submission_running = True
        self.last_submission_status = "sending"
        self.last_submission_error = None
        self._notify()
        try:
            await self._provider.async_submit_meter_reading(
                MeterReadingSubmission(
                    timestamp=local_now,
                    import_kwh=reading_value,
                    export_kwh=self.meter_value(FLOW_EXPORT) if self.export_entity else None,
                    meter_number=self.meter_number,
                )
            )
        except EAMNetzError as err:
            self.last_submission_status = "error"
            self.last_submission_error = str(err)
            _LOGGER.warning("EAM Netz meter reading submission failed: %s", err)
            result = False
        except Exception as err:  # noqa: BLE001 - keep HA alive on provider failures
            self.last_submission_status = "error"
            self.last_submission_error = str(err)
            _LOGGER.exception("Unexpected meter reading submission error")
            result = False
        else:
            self.last_submission_date = today
            self.last_submission_value = reading_value
            self.last_submission_status = "success"
            self.last_submission_error = None
            result = True
        finally:
            self._submission_running = False
            await self._async_save()
            self._notify()
        return result

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        @callback
        def remove_listener() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data.get("entity_id")
        flow = FLOW_IMPORT if entity_id == self.import_entity else FLOW_EXPORT
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        value = self._state_kwh(new_state.state, new_state.attributes.get("unit_of_measurement"))
        if value is None:
            return
        now = dt_util.now()
        previous = self._last_source[flow]
        if previous is not None and value >= previous:
            self._add_energy(flow, value - previous, now)
        self._last_source[flow] = value
        self._last_meter[flow] = value + self.offset(flow)
        await self._async_save()
        self._notify()

    async def _async_interval(self, now: datetime) -> None:
        self._roll_periods(now)
        await self._async_save()
        self._notify()

    def _add_energy(self, flow: str, energy: float, now: datetime) -> None:
        self._roll_periods(now)
        if flow == FLOW_IMPORT:
            self.day_import += energy
            self.month_import += energy
            self.year_import += energy
        else:
            self.day_export += energy
            self.month_export += energy
            self.year_export += energy

    def source_entity(self, flow: str) -> str | None:
        return self.import_entity if flow == FLOW_IMPORT else self.export_entity

    def offset(self, flow: str) -> float:
        return self.import_offset if flow == FLOW_IMPORT else self.export_offset

    def meter_value(self, flow: str) -> float:
        value = self._read_source(flow)
        if value is not None:
            self._last_meter[flow] = value + self.offset(flow)
        return self._last_meter[flow]

    def _read_source(self, flow: str) -> float | None:
        entity_id = self.source_entity(flow)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return self._state_kwh(state.state, state.attributes.get("unit_of_measurement"))

    @staticmethod
    def _state_kwh(state: str, unit: str | None) -> float | None:
        if state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            value = float(state)
        except (TypeError, ValueError):
            return None
        return value / 1000.0 if unit == UNIT_WH else value

    def _roll_periods(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        day_key = local.date().isoformat()
        month_key = f"{local.year:04d}-{local.month:02d}"
        year_key = self.billing_year_start(local).date().isoformat()
        if self._day_key and self._day_key != day_key:
            self.day_import = self.day_export = 0.0
        if self._month_key and self._month_key != month_key:
            self.month_import = self.month_export = 0.0
        if self._year_key and self._year_key != year_key:
            self.year_import = self.year_export = 0.0
        self._day_key, self._month_key, self._year_key = day_key, month_key, year_key

    def billing_year_start(self, now: datetime) -> datetime:
        local = dt_util.as_local(now)
        candidate = self._safe_local_datetime(local.year, self.billing_start_month, self.billing_start_day)
        if local < candidate:
            candidate = self._safe_local_datetime(local.year - 1, self.billing_start_month, self.billing_start_day)
        return candidate

    def billing_year_end(self, now: datetime) -> datetime:
        start = self.billing_year_start(now)
        return self._safe_local_datetime(start.year + 1, self.billing_start_month, self.billing_start_day)

    def period_start(self, period: str, now: datetime | None = None) -> datetime:
        local = dt_util.as_local(now or dt_util.now())
        if period == "day":
            return local.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "month":
            return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.billing_year_start(local)

    def average(self, flow: str, period: str, now: datetime | None = None) -> float:
        local = dt_util.as_local(now or dt_util.now())
        value = self.period_value(flow, period)
        elapsed_hours = max((local - self.period_start(period, local)).total_seconds() / 3600.0, 1 / 60)
        if period == "day":
            return value / elapsed_hours
        if period == "month":
            return value / max(elapsed_hours / 24.0, 1 / 24)
        return value / max(elapsed_hours / (24.0 * 30.436875), 1 / 730.485)

    def period_value(self, flow: str, period: str) -> float:
        return float(getattr(self, f"{period}_{'import' if flow == FLOW_IMPORT else 'export'}"))

    @staticmethod
    def _safe_local_datetime(year: int, month: int, day: int) -> datetime:
        safe_day = min(day, calendar.monthrange(year, month)[1])
        return dt_util.start_of_local_day(datetime(year, month, safe_day).date())

    async def _async_restore(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        for key in ("day_import", "day_export", "month_import", "month_export", "year_import", "year_export"):
            setattr(self, key, float(data.get(key, 0.0)))
        self._day_key = str(data.get("day_key", ""))
        self._month_key = str(data.get("month_key", ""))
        self._year_key = str(data.get("year_key", ""))
        for flow in (FLOW_IMPORT, FLOW_EXPORT):
            self._last_source[flow] = data.get(f"last_source_{flow}")
            self._last_meter[flow] = float(data.get(f"last_meter_{flow}", 0.0))
        self.auto_submission_enabled = bool(data.get("auto_submission_enabled", False))
        self.last_submission_date = data.get("last_submission_date")
        value = data.get("last_submission_value")
        self.last_submission_value = float(value) if value is not None else None
        self.last_submission_status = str(data.get("last_submission_status", "never"))
        self.last_submission_error = data.get("last_submission_error")

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "day_import": self.day_import,
                "day_export": self.day_export,
                "month_import": self.month_import,
                "month_export": self.month_export,
                "year_import": self.year_import,
                "year_export": self.year_export,
                "day_key": self._day_key,
                "month_key": self._month_key,
                "year_key": self._year_key,
                "last_source_import": self._last_source[FLOW_IMPORT],
                "last_source_export": self._last_source[FLOW_EXPORT],
                "last_meter_import": self._last_meter[FLOW_IMPORT],
                "last_meter_export": self._last_meter[FLOW_EXPORT],
                "auto_submission_enabled": self.auto_submission_enabled,
                "last_submission_date": self.last_submission_date,
                "last_submission_value": self.last_submission_value,
                "last_submission_status": self.last_submission_status,
                "last_submission_error": self.last_submission_error,
            }
        )
