"""Measurement and persistence manager for Stromzähler."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import calendar
import logging
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
    CONF_METER_READING,
    CONF_METER_READING_EXPORT,
    CONF_SOURCE_ENTITY,
    DEFAULT_BILLING_START_DAY,
    DEFAULT_BILLING_START_MONTH,
    SOURCE_ENERGY,
    SOURCE_POWER,
    STORE_KEY_PREFIX,
    STORE_VERSION,
    UNIT_KW,
    UNIT_KWH,
    UNIT_W,
    UNIT_WH,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class StromzaehlerManager:
    """Track import/export energy and billing periods."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self.source_entity = entry.options.get(
            CONF_SOURCE_ENTITY, entry.data[CONF_SOURCE_ENTITY]
        )
        self.billing_start_day = int(
            entry.options.get(
                CONF_BILLING_START_DAY,
                entry.data.get(CONF_BILLING_START_DAY, DEFAULT_BILLING_START_DAY),
            )
        )
        self.billing_start_month = int(
            entry.options.get(
                CONF_BILLING_START_MONTH,
                entry.data.get(CONF_BILLING_START_MONTH, DEFAULT_BILLING_START_MONTH),
            )
        )
        self._initial_import_meter = float(entry.data[CONF_METER_READING])
        self._initial_export_meter = float(entry.data.get(CONF_METER_READING_EXPORT, 0.0))

        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{STORE_KEY_PREFIX}.{entry.entry_id}"
        )
        self._unsubs: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []

        self.import_meter = self._initial_import_meter
        self.export_meter = self._initial_export_meter
        self.day_import = 0.0
        self.day_export = 0.0
        self.month_import = 0.0
        self.month_export = 0.0
        self.year_import = 0.0
        self.year_export = 0.0

        self._day_key = ""
        self._month_key = ""
        self._year_key = ""
        self._last_value: float | None = None
        self._last_seen: datetime | None = None
        self._source_type: str | None = None
        self._source_unit: str | None = None
        self._started = False

    async def async_start(self) -> None:
        """Restore state and start tracking the source entity."""
        await self._async_restore()
        now = dt_util.now()
        self._roll_periods(now)

        state = self.hass.states.get(self.source_entity)
        if state is not None:
            self._set_source_metadata(state.attributes.get("unit_of_measurement"))
            self._last_value = self._state_as_float(state.state)
            self._last_seen = now

        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.source_entity], self._async_source_changed
            )
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._async_interval, UPDATE_INTERVAL)
        )
        self._started = True
        await self._async_save()

    async def async_stop(self) -> None:
        """Stop tracking and persist the latest values."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._started:
            await self._async_integrate_to(dt_util.now())
            await self._async_save()
        self._started = False

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a sensor update listener."""
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
        """Process a source entity state change."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        now = dt_util.now()
        await self._async_integrate_to(now)

        value = self._state_as_float(new_state.state)
        if value is None:
            return

        self._set_source_metadata(new_state.attributes.get("unit_of_measurement"))
        if self._source_type == SOURCE_ENERGY and self._last_value is not None:
            delta_kwh = self._energy_delta_kwh(self._last_value, value)
            if delta_kwh > 0:
                self._add_energy(delta_kwh, 0.0, now)

        self._last_value = value
        self._last_seen = now
        await self._async_save()
        self._notify()

    async def _async_interval(self, now: datetime) -> None:
        """Integrate power periodically and roll period counters."""
        await self._async_integrate_to(now)
        self._roll_periods(now)
        await self._async_save()
        self._notify()

    async def _async_integrate_to(self, now: datetime) -> None:
        """Integrate the previous power value up to now."""
        if (
            self._source_type != SOURCE_POWER
            or self._last_value is None
            or self._last_seen is None
        ):
            self._roll_periods(now)
            return

        elapsed_hours = max((now - self._last_seen).total_seconds(), 0.0) / 3600.0
        if elapsed_hours <= 0:
            return

        power_kw = self._power_to_kw(self._last_value)
        energy_kwh = abs(power_kw) * elapsed_hours
        if power_kw >= 0:
            self._add_energy(energy_kwh, 0.0, now)
        else:
            self._add_energy(0.0, energy_kwh, now)
        self._last_seen = now

    def _add_energy(self, imported: float, exported: float, now: datetime) -> None:
        """Add energy to lifetime and active period counters."""
        self._roll_periods(now)
        self.import_meter += imported
        self.export_meter += exported
        self.day_import += imported
        self.day_export += exported
        self.month_import += imported
        self.month_export += exported
        self.year_import += imported
        self.year_export += exported

    def _roll_periods(self, now: datetime) -> None:
        """Reset counters when their calendar/billing period changes."""
        local = dt_util.as_local(now)
        day_key = local.date().isoformat()
        month_key = f"{local.year:04d}-{local.month:02d}"
        year_start = self.billing_year_start(local)
        year_key = year_start.date().isoformat()

        if self._day_key and self._day_key != day_key:
            self.day_import = 0.0
            self.day_export = 0.0
        if self._month_key and self._month_key != month_key:
            self.month_import = 0.0
            self.month_export = 0.0
        if self._year_key and self._year_key != year_key:
            self.year_import = 0.0
            self.year_export = 0.0

        self._day_key = day_key
        self._month_key = month_key
        self._year_key = year_key

    def billing_year_start(self, now: datetime) -> datetime:
        """Return start of the current configured billing year."""
        local = dt_util.as_local(now)
        candidate = self._safe_local_datetime(
            local.year, self.billing_start_month, self.billing_start_day
        )
        if local < candidate:
            candidate = self._safe_local_datetime(
                local.year - 1, self.billing_start_month, self.billing_start_day
            )
        return candidate

    def billing_year_end(self, now: datetime) -> datetime:
        """Return start of the next configured billing year."""
        start = self.billing_year_start(now)
        return self._safe_local_datetime(
            start.year + 1, self.billing_start_month, self.billing_start_day
        )

    def period_start(self, period: str, now: datetime | None = None) -> datetime:
        """Return the start of a reporting period."""
        local = dt_util.as_local(now or dt_util.now())
        if period == "day":
            return local.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "month":
            return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.billing_year_start(local)

    def average(self, flow: str, period: str, now: datetime | None = None) -> float:
        """Return energy averaged over elapsed sub-periods."""
        local = dt_util.as_local(now or dt_util.now())
        value = self.period_value(flow, period)
        start = self.period_start(period, local)
        elapsed_hours = max((local - start).total_seconds() / 3600.0, 1 / 60)

        if period == "day":
            return value / elapsed_hours
        if period == "month":
            return value / max(elapsed_hours / 24.0, 1 / 24)
        return value / max(elapsed_hours / (24.0 * 30.436875), 1 / 730.485)

    def period_value(self, flow: str, period: str) -> float:
        """Return the active counter for a flow and period."""
        prefix = "import" if flow == "import" else "export"
        return float(getattr(self, f"{period}_{prefix}"))

    def meter_value(self, flow: str) -> float:
        """Return the virtual physical meter value for a flow."""
        return self.import_meter if flow == "import" else self.export_meter

    @property
    def source_type(self) -> str | None:
        """Return detected source type."""
        return self._source_type

    @property
    def source_unit(self) -> str | None:
        """Return detected source unit."""
        return self._source_unit

    def _set_source_metadata(self, unit: str | None) -> None:
        if unit in (UNIT_W, UNIT_KW):
            self._source_type = SOURCE_POWER
            self._source_unit = unit
        elif unit in (UNIT_WH, UNIT_KWH):
            self._source_type = SOURCE_ENERGY
            self._source_unit = unit
        elif unit:
            _LOGGER.warning(
                "Unsupported unit %s for smart meter entity %s",
                unit,
                self.source_entity,
            )

    def _power_to_kw(self, value: float) -> float:
        return value / 1000.0 if self._source_unit == UNIT_W else value

    def _energy_delta_kwh(self, previous: float, current: float) -> float:
        delta = current - previous
        if delta < 0:
            _LOGGER.info(
                "Energy source %s decreased; treating this as a reset",
                self.source_entity,
            )
            return 0.0
        return delta / 1000.0 if self._source_unit == UNIT_WH else delta

    @staticmethod
    def _state_as_float(state: str) -> float | None:
        if state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            return float(state)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_local_datetime(year: int, month: int, day: int) -> datetime:
        max_day = calendar.monthrange(year, month)[1]
        safe_day = min(day, max_day)
        return dt_util.start_of_local_day(
            datetime(year, month, safe_day).date()
        )

    async def _async_restore(self) -> None:
        data = await self._store.async_load()
        if not data:
            return

        self.import_meter = float(data.get("import_meter", self.import_meter))
        self.export_meter = float(data.get("export_meter", self.export_meter))
        self.day_import = float(data.get("day_import", 0.0))
        self.day_export = float(data.get("day_export", 0.0))
        self.month_import = float(data.get("month_import", 0.0))
        self.month_export = float(data.get("month_export", 0.0))
        self.year_import = float(data.get("year_import", 0.0))
        self.year_export = float(data.get("year_export", 0.0))
        self._day_key = str(data.get("day_key", ""))
        self._month_key = str(data.get("month_key", ""))
        self._year_key = str(data.get("year_key", ""))
        self._last_value = data.get("last_value")
        self._source_type = data.get("source_type")
        self._source_unit = data.get("source_unit")

        last_seen = data.get("last_seen")
        if last_seen:
            try:
                self._last_seen = datetime.fromisoformat(last_seen)
            except ValueError:
                self._last_seen = None

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "import_meter": self.import_meter,
                "export_meter": self.export_meter,
                "day_import": self.day_import,
                "day_export": self.day_export,
                "month_import": self.month_import,
                "month_export": self.month_export,
                "year_import": self.year_import,
                "year_export": self.year_export,
                "day_key": self._day_key,
                "month_key": self._month_key,
                "year_key": self._year_key,
                "last_value": self._last_value,
                "last_seen": self._last_seen.isoformat() if self._last_seen else None,
                "source_type": self._source_type,
                "source_unit": self._source_unit,
            }
        )
