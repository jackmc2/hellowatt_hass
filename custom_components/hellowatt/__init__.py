"""The HelloWatt integration."""

from __future__ import annotations

from datetime import datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .client import HelloWattApiClient
from .const import DOMAIN, LOGGER
from .coordinator import HelloWattCoordinator
from .importer import (
    SERVICE_CLEAR_SCHEMA,
    SERVICE_CLEAR_STATISTICS,
    SERVICE_IMPORT_HISTORICAL,
    SERVICE_IMPORT_SCHEMA,
    async_clear_statistics,
    async_import_historical_data,
)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# HelloWatt rate-limits login attempts with HTTP 429. Keep a local cooldown so
# Home Assistant's automatic config-entry retries do not repeatedly hit the
# login endpoint while the remote rate limit is still active.
RATE_LIMIT_COOLDOWN = timedelta(hours=1)
_rate_limit_until: datetime | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HelloWatt from a config entry."""
    global _rate_limit_until

    now = dt_util.utcnow()
    if _rate_limit_until is not None and now < _rate_limit_until:
        remaining = max(1, int((_rate_limit_until - now).total_seconds()))
        raise ConfigEntryNotReady(
            f"HelloWatt login rate limit cooldown active "
            f"({remaining} seconds remaining)"
        )

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    # Create a dedicated session with cookie jar for this integration
    session = async_create_clientsession(
        hass, cookie_jar=aiohttp.CookieJar(unsafe=True)
    )
    client = HelloWattApiClient(session, username, password)

    try:
        await client.authenticate()
    except aiohttp.ClientResponseError as err:
        if err.status == 429:
            _rate_limit_until = dt_util.utcnow() + RATE_LIMIT_COOLDOWN
            LOGGER.warning(
                "HelloWatt login rate-limited (HTTP 429); "
                "suppressing new login attempts for %s",
                RATE_LIMIT_COOLDOWN,
            )
            raise ConfigEntryNotReady(
                "HelloWatt login temporarily rate-limited (HTTP 429)"
            ) from err
        raise
    except Exception as err:
        error_str = str(err).lower()
        # Check if the error is authentication-related
        if "authentication failed" in error_str or "no session cookie" in error_str:
            raise ConfigEntryAuthFailed(
                "Authentication failed. Please reauthenticate."
            ) from err
        raise

    # Authentication succeeded: clear any stale cooldown.
    _rate_limit_until = None

    # Create coordinators for each home/PDL
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"client": client, "coordinators": {}}

    if not client.homes:
        LOGGER.warning(
            "HelloWatt: no homes found for this account — no sensors will be created"
        )

    for home in client.homes:
        pdl = home.get("enedisHome", {}).get("pdl")
        home_id = home.get("id")
        if pdl and home_id:
            coordinator = HelloWattCoordinator(hass, client, entry, pdl, home_id, home)
            await coordinator.async_config_entry_first_refresh()
            hass.data[DOMAIN][entry.entry_id]["coordinators"][pdl] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for options changes
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Register the services once
    if not hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORICAL):

        async def handle_import(call: ServiceCall) -> None:
            try:
                await async_import_historical_data(hass, call)
            except Exception as err:
                LOGGER.exception("Unhandled error in import_historical_data: %s", err)
                raise

        hass.services.async_register(
            DOMAIN,
            SERVICE_IMPORT_HISTORICAL,
            handle_import,
            schema=SERVICE_IMPORT_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_STATISTICS):

        async def handle_clear(call: ServiceCall) -> None:
            try:
                await async_clear_statistics(hass, call)
            except Exception as err:
                LOGGER.exception("Unhandled error in clear_statistics: %s", err)
                raise

        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_STATISTICS,
            handle_clear,
            schema=SERVICE_CLEAR_SCHEMA,
        )

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change.

    Args:
        hass: Home Assistant instance
        entry: Config entry that was updated
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        # Unregister services if no more entries
        if not hass.data[DOMAIN]:
            if hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORICAL):
                hass.services.async_remove(DOMAIN, SERVICE_IMPORT_HISTORICAL)
            if hass.services.has_service(DOMAIN, SERVICE_CLEAR_STATISTICS):
                hass.services.async_remove(DOMAIN, SERVICE_CLEAR_STATISTICS)

    return unload_ok
