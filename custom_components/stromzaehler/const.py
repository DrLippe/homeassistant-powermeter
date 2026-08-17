"""Constants for the Stromzähler integration."""

from datetime import timedelta

DOMAIN = "stromzaehler"
PLATFORMS = ["sensor"]

# Legacy source key kept for config-entry migration/backwards compatibility.
CONF_SOURCE_ENTITY = "source_entity"
CONF_IMPORT_ENTITY = "import_entity"
CONF_EXPORT_ENTITY = "export_entity"
CONF_METER_READING = "meter_reading"
CONF_METER_READING_EXPORT = "meter_reading_export"
CONF_IMPORT_OFFSET = "import_offset"
CONF_EXPORT_OFFSET = "export_offset"
CONF_BILLING_START_DAY = "billing_start_day"
CONF_BILLING_START_MONTH = "billing_start_month"

DEFAULT_NAME = "Stromzähler"
DEFAULT_BILLING_START_DAY = 1
DEFAULT_BILLING_START_MONTH = 1

UPDATE_INTERVAL = timedelta(minutes=1)
STORE_VERSION = 1
STORE_KEY_PREFIX = f"{DOMAIN}.state"

FLOW_IMPORT = "import"
FLOW_EXPORT = "export"

UNIT_WH = "Wh"
UNIT_KWH = "kWh"

ATTR_PERIOD_START = "period_start"
ATTR_PERIOD_END = "period_end"
ATTR_SOURCE_ENTITY = "source_entity"
ATTR_OFFSET = "offset"
ATTR_FLOW = "flow"
