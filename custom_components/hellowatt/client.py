"""API Client for HelloWatt."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, TypeVar, cast

import aiohttp

from .const import API_URL, LOGGER

HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Accept": "application/json; version=1.48",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.hellowatt.fr/mon-compte/",
}

# Retry configuration for ordinary API requests. Authentication deliberately
# does not retry HTTP 429 responses so the config-entry layer can immediately
# persist and enforce the server-side cooldown.
MAX_RETRIES = 3
BACKOFF_BASE = 2

# Type variable for generic return types
T = TypeVar("T")


class HelloWattApiClient:
    """HelloWatt API Client."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        """Initialize the API client.

        Args:
            session: aiohttp ClientSession with cookie jar enabled
            username: HelloWatt account username/email
            password: HelloWatt account password
        """
        self._session: aiohttp.ClientSession = session
        self._username: str = username
        self._password: str = password
        self._homes: list[dict[str, Any]] = []
        self._authenticating: bool = False

    @property
    def homes(self) -> list[dict[str, Any]]:
        """Return the list of homes/PDLs associated with the account."""
        return self._homes

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers with CSRF token if available.

        Returns:
            Dictionary of HTTP headers including CSRF token from cookies
        """
        headers = HEADERS.copy()
        for cookie in self._session.cookie_jar:
            if cookie.key == "csrftoken":
                headers["x-csrftoken"] = cookie.value
                break
        return headers

    @staticmethod
    def _raise_for_auth_response(
        response: aiohttp.ClientResponse, operation: str
    ) -> None:
        """Raise authentication HTTP errors without retrying rate limits."""
        if response.status == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                LOGGER.warning(
                    "%s returned 429; authentication will not retry "
                    "(Retry-After=%s)",
                    operation,
                    retry_after,
                )
            else:
                LOGGER.warning(
                    "%s returned 429; authentication will not retry",
                    operation,
                )

        response.raise_for_status()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        handle_500_as_no_data: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Make HTTP request with automatic retry on 403 (authentication failure).

        This method centralizes the retry logic that was previously duplicated
        across all API methods. It automatically re-authenticates and retries
        once if a 403 status is received.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL to request
            handle_500_as_no_data: If True, treat 500 errors as missing data
                (used for gas endpoints when no contract exists)
            **kwargs: Additional arguments to pass to the request

        Returns:
            JSON response as dictionary

        Raises:
            Exception: If request fails after retry or for non-retryable errors
        """
        # Ensure headers are included
        if "headers" not in kwargs:
            kwargs["headers"] = self._get_headers()

        attempts = 0
        while True:
            async with self._session.request(method, url, **kwargs) as response:
                # Handle rate limiting (429) with backoff and respect Retry-After
                if response.status == 429:
                    attempts += 1
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait = (
                            int(retry_after)
                            if retry_after is not None
                            else BACKOFF_BASE**attempts
                        )
                    except Exception:
                        wait = BACKOFF_BASE**attempts

                    if attempts >= MAX_RETRIES:
                        LOGGER.error("Max retries reached for %s, status=429", url)
                        response.raise_for_status()

                    LOGGER.warning(
                        "Received 429 for %s, retrying after %s seconds (attempt %s)",
                        url,
                        wait,
                        attempts,
                    )
                    await asyncio.sleep(wait)
                    # retry the loop
                    continue

                # Handle authentication failure - retry once after re-authenticating
                if response.status == 403:
                    LOGGER.debug("Received 403, attempting re-authentication")
                    await self.authenticate()

                    # Refresh headers after authentication
                    kwargs["headers"] = self._get_headers()

                    # Retry the request once after re-authenticating
                    async with self._session.request(
                        method, url, **kwargs
                    ) as retry_response:
                        return await self._handle_response(
                            retry_response, url, handle_500_as_no_data
                        )

                return await self._handle_response(response, url, handle_500_as_no_data)

    async def _handle_response(
        self,
        response: aiohttp.ClientResponse,
        url: str,
        handle_500_as_no_data: bool = False,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Handle HTTP response with appropriate error logging.

        Args:
            response: aiohttp ClientResponse object
            url: URL that was requested (for logging)
            handle_500_as_no_data: If True, treat 500 errors as missing data

        Returns:
            JSON response as dictionary

        Raises:
            Exception: For HTTP errors or invalid responses
        """
        # Special handling for 500 errors on gas endpoint (no contract)
        if response.status == 500 and handle_500_as_no_data:
            # Use DEBUG level - this is expected for accounts without gas contracts
            LOGGER.debug(
                "Gas data not available (status 500) - likely no gas contract for URL %s",
                url,
            )
            raise Exception("No gas contract available")

        # Log non-200 responses with truncated response text
        if response.status != 200:
            response_text = await response.text()
            LOGGER.error(
                "API error for %s: status=%s, response=%s",
                url,
                response.status,
                response_text[:500],  # Limit to 500 chars
            )

        response.raise_for_status()
        result: dict[str, Any] | list[dict[str, Any]] = await response.json()
        return result

    async def authenticate(self) -> None:
        """Authenticate with HelloWatt API and fetch homes.

        This method performs a multi-step authentication process:
        1. Gets CSRF token from login page
        2. Posts credentials with CSRF token
        3. Validates session cookie was received
        4. Fetches list of homes/PDLs

        HTTP 429 responses are never retried here. They are raised immediately
        so the Home Assistant config-entry layer can persist and enforce the
        requested cooldown before any further login attempt.

        Raises:
            Exception: If authentication fails or no session cookie received
        """
        if self._authenticating:
            # Prevent recursive authentication attempts
            LOGGER.debug("Authentication already in progress, skipping")
            return

        self._authenticating = True
        try:
            login_url = "https://www.hellowatt.fr/accounts/login/"

            # 1. Get login page to obtain CSRF cookie.
            async with self._session.get(login_url) as response:
                self._raise_for_auth_response(response, "Login GET")

            # Extract CSRF token from cookie jar
            csrftoken = ""
            for cookie in self._session.cookie_jar:
                if cookie.key == "csrftoken":
                    csrftoken = cookie.value
                    break

            # 2. Post login credentials with CSRF token.
            data = {
                "login": self._username,
                "password": self._password,
                "csrfmiddlewaretoken": csrftoken,
            }

            headers = {
                "User-Agent": HEADERS["User-Agent"],
                "Referer": login_url,
            }

            async with self._session.post(
                login_url, data=data, headers=headers
            ) as response:
                self._raise_for_auth_response(response, "Login POST")

                # Parse JSON response for error messages
                resp_json: dict[str, Any] | None = None
                try:
                    resp_json = await response.json()
                except (aiohttp.ContentTypeError, ValueError):
                    pass

                if resp_json:
                    form = resp_json.get("form", {})
                    if form.get("errors"):
                        raise Exception(f"Authentication failed: {form['errors']}")
                    for field_name, field_data in form.get("fields", {}).items():
                        if field_data.get("errors"):
                            raise Exception(
                                f"Authentication failed ({field_name}): {field_data['errors']}"
                            )

                # Verify session cookie was set
                if not any(
                    cookie.key == "sessionid" for cookie in self._session.cookie_jar
                ):
                    raise Exception("Authentication failed: No session cookie received")

            # 3. Fetch homes after successful authentication.
            url = f"{API_URL}/homes"
            async with self._session.get(
                url, headers=self._get_headers()
            ) as response:
                self._raise_for_auth_response(response, "Homes GET")
                self._homes = await response.json()

            # Use INFO for successful authentication - important for troubleshooting
            LOGGER.info("Authentication successful, found %d home(s)", len(self._homes))
        finally:
            self._authenticating = False

    async def get_daily_consumption(
        self, home_id: str, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Get daily electricity consumption data.

        Args:
            home_id: Home identifier from HelloWatt API
            start_date: Start date for consumption data (timezone-aware)
            end_date: End date for consumption data (timezone-aware)

        Returns:
            Dictionary containing daily consumption values with structure:
            {
                "values": [
                    {
                        "datetime": "2024-01-01T00:00:00Z",
                        "kwhDetailed": {"HP": 10.5, "HC": 5.2},
                        "eurosDetailed": {"consumption": 2.5, "subscription": 0.5},
                        "valueCo2": 1.2
                    },
                    ...
                ]
            }
        """
        url = f"{API_URL}/homes/{home_id}/sge_measures/conso_daily"
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }

        result = await self._request_with_retry("GET", url, params=params)
        return cast("dict[str, Any]", result)

    async def get_daily_gas_consumption(
        self, home_id: str, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Get daily gas consumption data.

        Args:
            home_id: Home identifier from HelloWatt API
            start_date: Start date for consumption data (timezone-aware)
            end_date: End date for consumption data (timezone-aware)

        Returns:
            Dictionary containing daily gas consumption values

        Raises:
            Exception: If no gas contract exists (500 error) or other API errors
        """
        url = f"{API_URL}/homes/{home_id}/adict_measures/conso_daily"
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }

        result = await self._request_with_retry(
            "GET", url, params=params, handle_500_as_no_data=True
        )
        return cast("dict[str, Any]", result)

    async def get_yearly_temperature(
        self, home_id: str, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Get yearly temperature data.

        Args:
            home_id: Home identifier from HelloWatt API
            start_date: Start date for temperature data (timezone-aware)
            end_date: End date for temperature data (timezone-aware)

        Returns:
            Dictionary containing monthly temperature values
        """
        url = f"{API_URL}/homes/{home_id}/temperature_measures/yearly"
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }

        result = await self._request_with_retry("GET", url, params=params)
        return cast("dict[str, Any]", result)

    async def get_contracts(self, home_id: str) -> list[dict[str, Any]]:
        """Get energy contracts for a home.

        Args:
            home_id: Home identifier from HelloWatt API

        Returns:
            List of contracts with provider and offer information
        """
        url = f"{API_URL}/homes/{home_id}/contracts"
        result = await self._request_with_retry("GET", url)
        return cast("list[dict[str, Any]]", result)

    async def get_homes(self) -> list[dict[str, Any]]:
        """Get list of homes/PDLs associated with the account.

        Returns:
            List of homes with address and PDL information
        """
        url = f"{API_URL}/homes"
        result = await self._request_with_retry("GET", url)
        return cast("list[dict[str, Any]]", result)
