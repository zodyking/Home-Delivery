"""Constants for Home Delivery integration."""
from datetime import timedelta

DOMAIN = "home_delivery"

# Config entry keys
CONF_ADDON_URL = "addon_url"

# Default addon URL (internal Docker network)
DEFAULT_ADDON_URL = "http://local-home-delivery:8000"

# Update interval for coordinator
SCAN_INTERVAL = timedelta(minutes=5)

# Addon slug for Supervisor API
ADDON_SLUG = "local_home_delivery"
