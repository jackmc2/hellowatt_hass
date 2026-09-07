# HelloWatt Home Assistant Integration — jackmc2 fork

Custom Home Assistant integration for monitoring energy consumption data from HelloWatt.

> [!IMPORTANT]
> This repository is a fork of [`homeassistant-fr-ecosystem/hellowatt_hass`](https://github.com/homeassistant-fr-ecosystem/hellowatt_hass).
>
> The purpose of this fork is to keep the upstream integration while adding protective fixes for HelloWatt authentication rate limiting and Home Assistant historical statistics handling.

## Why this fork exists

The upstream integration authenticates against `https://www.hellowatt.fr/accounts/login/` when the Home Assistant config entry is loaded.

When HelloWatt rate-limits login attempts and returns HTTP `429`, repeated Home Assistant setup retries, manual reloads, or restarts can generate additional login attempts while the remote limit is still active.

This fork adds a local rate-limit cooldown in the integration setup path:

- when authentication receives HTTP `429`, the config entry is reported as temporarily unavailable with `ConfigEntryNotReady`;
- new setup attempts are suppressed locally for **1 hour** in the current Home Assistant process;
- successful authentication clears the cooldown;
- the normal authentication-error handling for invalid credentials remains unchanged.

The fork also includes historical-statistics fixes used by the Home Assistant energy dashboard:

- historical statistics are imported as **sum-only** statistics (`StatisticMeanType.NONE`);
- historical imports may include data up to **D-1** when available from HelloWatt;
- cumulative sums are seeded from the statistics immediately preceding the requested import range so partial re-imports keep continuity;
- the daily electricity sensor remains `SensorStateClass.TOTAL`.

Current fork version:

```text
1.0.2-jackmc2
```

### Important limitation

The HTTP 429 cooldown is stored in memory. Restarting Home Assistant clears it. Therefore, if HelloWatt is actively rate-limiting the account, repeatedly restarting or manually reloading the integration can still defeat the protection.

The recommended behaviour after a `429` is to leave Home Assistant running and allow the integration to recover without repeated manual reloads.

## Upstream compatibility

This fork is intentionally kept as close as possible to the original project:

- upstream repository: <https://github.com/homeassistant-fr-ecosystem/hellowatt_hass>
- fork repository: <https://github.com/jackmc2/hellowatt_hass>
- Home Assistant domain remains `hellowatt`;
- sensor names and services remain compatible with the upstream integration.

The fork should be rebased/synchronised with upstream updates carefully so the fork-specific protections are not lost.

## Features

### Electricity monitoring

- Daily electricity consumption (kWh)
- Peak hours (HP) and off-peak hours (HC) consumption for dual-rate contracts
- Yesterday's consumption
- Weekly consumption total
- CO2 emissions tracking
- Cost breakdown

### Gas monitoring

- Daily gas consumption (kWh)
- Yesterday's consumption
- Weekly consumption total
- CO2 emissions tracking
- Cost breakdown

### Additional features

- Temperature monitoring
- Historical data import service
- Multi-home / multi-PDL support
- Automatic session management with re-authentication
- Contract information
- Home Assistant diagnostics
- System health reporting

## Installation with HACS

This fork is installed as a **custom HACS repository**.

### 1. Add the custom repository

In Home Assistant:

1. Open **HACS**.
2. Open the menu **⋮**.
3. Select **Custom repositories**.
4. Add:

```text
https://github.com/jackmc2/hellowatt_hass
```

5. Select category **Integration**.
6. Add the repository.

HACS should display the fork as:

```text
Hellowatt (jackmc2 fork)
@jackmc2
```

### 2. Install the integration

Install **Hellowatt (jackmc2 fork)** from HACS, then restart Home Assistant once.

If the upstream HelloWatt repository was previously installed through HACS, make sure the fork is the repository currently installed before restarting.

## Manual installation

Copy the directory:

```text
custom_components/hellowatt
```

into:

```text
<home-assistant-config>/custom_components/hellowatt
```

Then restart Home Assistant.

## Configuration

1. Open **Settings → Devices & services**.
2. Select **Add Integration**.
3. Search for **HelloWatt**.
4. Enter the HelloWatt account email and password.

The integration automatically discovers the homes/PDLs associated with the account.

### Options

The integration supports configurable polling and lookback periods.

| Option | Default | Description |
|---|---:|---|
| Update interval | 1 hour | Frequency of HelloWatt API refreshes |
| Data recovery period | 7 days | Number of days fetched per update to recover missed data |

## Main sensors

Entity IDs include the PDL when created by Home Assistant. The exact generated IDs can therefore differ between installations.

### Electricity

- Daily electricity consumption
- Peak-hours consumption
- Off-peak-hours consumption
- Previous-day consumption
- Weekly consumption
- CO2 emissions
- Daily cost
- Consumption cost
- Subscription cost

### Gas

Equivalent sensors are created when a gas contract is available.

### Diagnostics

Contract provider and offer information are exposed as diagnostic entities and may be hidden by default in the Home Assistant UI.

## Services

### `hellowatt.import_historical_data`

Imports historical HelloWatt consumption into Home Assistant long-term statistics.

Example:

```yaml
service: hellowatt.import_historical_data
data:
  start_date: "2026-08-01"
  end_date: "2026-08-31"
  pdl: "12345678901234"
```

Parameters:

- `start_date`: required, `YYYY-MM-DD`
- `end_date`: optional, `YYYY-MM-DD`; dates newer than D-1 are automatically limited to D-1
- `pdl`: optional; leave empty to process all available PDLs

Historical data is imported month by month to reduce API load. The imported metadata is sum-only (`has_sum=True`, `has_mean=False`, `StatisticMeanType.NONE`), and existing cumulative sums immediately before the import window are reused to preserve continuity during partial re-imports.

### `hellowatt.clear_statistics`

Clears HelloWatt historical statistics from Home Assistant.

Example:

```yaml
service: hellowatt.clear_statistics
data:
  pdl: "12345678901234"
```

Use this only when intentionally rebuilding HelloWatt statistics.

## HTTP 429 troubleshooting

A typical HelloWatt rate-limit failure looks like:

```text
Login GET returned 429
ClientResponseError: 429, message='Too Many Requests'
```

With this fork, the first HTTP `429` encountered during config-entry authentication activates a one-hour local cooldown and Home Assistant treats the integration as temporarily unavailable instead of treating the setup as a permanent authentication failure.

During the cooldown, logs may contain a message similar to:

```text
HelloWatt login rate limit cooldown active
```

or:

```text
HelloWatt login temporarily rate-limited (HTTP 429)
```

### What to do after a 429

- Do not repeatedly reload the HelloWatt integration.
- Do not repeatedly restart Home Assistant.
- Leave Home Assistant running and allow the remote rate limit to expire.
- Verify later that the HelloWatt sensors become available again.

The actual duration of HelloWatt's server-side rate limit is controlled by HelloWatt and is not known by this integration.

## Data update behaviour

- Default polling interval: 1 hour
- HelloWatt data can arrive with a delay depending on Enedis/provider availability
- Expired sessions are re-authenticated automatically
- Historical statistics can be imported with the dedicated service up to D-1 when data is available

## Architecture

```text
custom_components/hellowatt/
├── __init__.py           # Integration setup, rate-limit cooldown, services
├── client.py             # HelloWatt API client and authentication
├── config_flow.py        # Configuration UI
├── const.py              # Constants
├── coordinator.py        # Periodic data updates
├── diagnostics.py        # Home Assistant diagnostics
├── importer.py           # Historical import and statistics management
├── manifest.json         # Integration metadata
├── sensor.py             # Sensor entities
├── services.yaml         # Service definitions
├── strings.json          # UI strings
└── system_health.py      # System health reporting
```

## Fork-specific changes

### `1.0.2-jackmc2`

- Import long-term statistics with `StatisticMeanType.NONE` instead of arithmetic means.
- Allow historical import up to D-1 when data is available.
- Keep cumulative statistical sums continuous during partial re-imports.
- Keep `electricity_daily` as `SensorStateClass.TOTAL`.

### `1.0.1-jackmc2`

- Handle HTTP `429` during config-entry authentication as temporary unavailability.
- Add a local one-hour authentication cooldown after a `429`.
- Prevent Home Assistant automatic setup retries from repeatedly hitting HelloWatt during that cooldown.
- Identify the HACS repository as **Hellowatt (jackmc2 fork)**.
- Keep the Home Assistant integration domain `hellowatt` for compatibility.

## Development and contributions

For general integration improvements, prefer contributing to the upstream project whenever possible:

<https://github.com/homeassistant-fr-ecosystem/hellowatt_hass>

Fork-specific changes can be tracked in this repository.

## License

This fork retains the license of the upstream project. See the `LICENSE` file.

## Credits

Original project: [`homeassistant-fr-ecosystem/hellowatt_hass`](https://github.com/homeassistant-fr-ecosystem/hellowatt_hass).

Fork maintenance and additional protections: [`jackmc2/hellowatt_hass`](https://github.com/jackmc2/hellowatt_hass).

## Disclaimer

This is an unofficial integration and is not affiliated with or endorsed by HelloWatt. Use it at your own risk.