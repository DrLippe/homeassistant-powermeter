"""Measurement and persistence manager for Stromzähler."""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BILLING_START_DAY,
    CONF_BILLING_START_MONTH,
    CONF_EXPORT_ENTITY,
    CONF_EXPORT_OFFSET,
    CONF_IMPORT_ENTITY,
    CONF_IMPORT_OFFSET,
    DEFAULT_BILLING_START_DAY,
    DEFAULT_BILLING_START_MONTH,
    FLOW_EXPORT,
    FLOW_IMPORT,
    STORE_KEY_PREFIX,
    STORE_VERSION,
    UNIT_WH,
    UPDATE_INTERVAL,
)


class StromzaehlerManager:
    """Track cumulative import/export entities and active reporting periods."""

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
        await self._async_save()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._async_save()

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
            }
        )
