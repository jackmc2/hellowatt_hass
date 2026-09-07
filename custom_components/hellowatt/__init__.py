"""The HelloWatt integration."""

from __future__ import annotations

from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
import math
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

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

# HelloWatt rate-limits login attempts with HTTP 429. Persist the cooldown so
# Home Assistant reloads and restarts cannot accidentally bypass it.
RATE_LIMIT_COOLDOWN = timedelta(hours=1)
RATE_LIMIT_STORAGE_VERSION = 1
RATE_LIMIT_STORAGE_KEY = f"{DOMAIN}.rate_limit"
RATE_LIMIT_DATA_KEY = f"{DOMAIN}_rate_limit_state"


async def _async_get_rate_limit_state(hass: HomeAssistant) -> dict[str, Any]:
    """Load and cache persisted rate-limit state."""
    state = hass.data.get(RATE_LIMIT_DATA_KEY)
    if state is not None:
        return state

    store = Store(hass, RATE_LIMIT_STORAGE_VERSION, RATE_LIMIT_STORAGE_KEY)
    saved = await store.async_load() or {}
    raw_entries = saved.get("entries", {})

    entries: dict[str, float] = {}
    if isinstance(raw_entries, dict):
        for entry_id, value in raw_entries.items():
            try:
                entries[str(entry_id)] = float(value)
            except (TypeError, ValueError):
                continue

    state = {"store": store, "entries": entries}
    hass.data[RATE_LIMIT_DATA_KEY] = state
    return state


async def _async_save_rate_limit_state(
    state: dict[str, Any],
) -> None:
    """Persist rate-limit state without breaking setup if storage fails."""
    try:
        await state["store"].async_save({"entries": state["entries"]})
    except Exception as err:  # pragma: no cover - defensive persistence fallback
        LOGGER.warning("Unable to persist HelloWatt rate-limit state: %s", err)


async def _async_get_rate_limit_remaining(
    hass: HomeAssistant, entry_id: str
) -> int:
    """Return remaining persisted cooldown in seconds for a config entry."""
    state = await _async_get_rate_limit_state(hass)
    entries: dict[str, float] = state["entries"]
    until = entries.get(entry_id)

    if until is None:
        return 0

    remaining = math.ceil(until - time.time())
    if remaining > 0:
        return remaining

    entries.pop(entry_id, None)
    await _async_save_rate_limit_state(state)
    return 0


async def _async_set_rate_limit(
    hass: HomeAssistant, entry_id: str, seconds: int
) -> None:
    """Persist a cooldown deadline for a config entry."""
    state = await _async_get_rate_limit_state(hass)
    entries: dict[str, float] = state["entries"]
    entries[entry_id] = time.time() + max(1, seconds)
    await _async_save_rate_limit_state(state)


async def _async_clear_rate_limit(hass: HomeAssistant, entry_id: str) -> None:
    """Clear any persisted cooldown after successful authentication."""
    state = await _async_get_rate_limit_state(hass)
    entries: dict[str, float] = state["entries"]

    if entry_id not in entries:
        return

    entries.pop(entry_id, None)
    await _async_save_rate_limit_state(state)


def _rate_limit_cooldown_seconds(err: aiohttp.ClientResponseError) -> int:
    """Return Retry-After duration or the one-hour fallback."""
    fallback = int(RATE_LIMIT_COOLDOWN.total_seconds())
    headers = err.headers

    if not headers:
        return fallback

    retry_after = headers.get("Retry-After")
    if not retry_after:
        return fallback

    # Retry-After may be either a number of seconds or an HTTP date.
    try:
        return max(1, math.ceil(float(retry_after)))
    except (TypeError, ValueError):
        pass

    try:
        retry_at = parsedate_to_datetime(retry_after)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(1, math.ceil(retry_at.timestamp() - time.time()))
    except (TypeError, ValueError, OverflowError):
        return fallback


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HelloWatt from a config entry."""
    remaining = await _async_get_rate_limit_remaining(hass, entry.entry_id)
    if remaining > 0:
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
            cooldown_seconds = _rate_limit_cooldown_seconds(err)
            await _async_set_rate_limit(hass, entry.entry_id, cooldown_seconds)
            LOGGER.warning(
                "HelloWatt login rate-limited (HTTP 429); "
                "suppressing new login attempts for %s seconds",
                cooldown_seconds,
            )
            raise ConfigEntryNotReady(
                f"HelloWatt login temporarily rate-limited (HTTP 429); "
                f"cooldown {cooldown_seconds} seconds"
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

    # Authentication succeeded: clear any stale persisted cooldown.
    await _async_clear_rate_limit(hass, entry.entry_id)

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
