"""Sensor platform for HelloWatt."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfMass,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HelloWattCoordinator

# Define sensor configurations with metadata
# Added suggested_display_precision for better Energy Dashboard display
SENSOR_TYPES: dict[str, dict[str, Any]] = {
    "electricity": {
        "name": "Electricity Daily",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:lightning-bolt",
        "suggested_display_precision": 2,
    },
    "electricity_peak": {
        "name": "Electricity Peak Hours Daily",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:weather-sunny",
        "suggested_display_precision": 2,
    },
    "electricity_off_peak": {
        "name": "Electricity Off-Peak Hours Daily",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:weather-night",
        "suggested_display_precision": 2,
    },
    "electricity_yesterday": {
        "name": "Electricity Day Before",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:calendar-minus",
        "suggested_display_precision": 2,
    },
    "electricity_weekly": {
        "name": "Electricity Weekly",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:calendar-week",
        "suggested_display_precision": 1,
    },
    "gas": {
        "name": "Gas Daily",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:fire",
        "suggested_display_precision": 2,
    },
    "gas_yesterday": {
        "name": "Gas Day Before",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:fire-circle",
        "suggested_display_precision": 2,
    },
    "gas_weekly": {
        "name": "Gas Weekly",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:fire-alert",
        "suggested_display_precision": 1,
    },
    "temperature": {
        "name": "Temperature",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": None,
        "suggested_display_precision": 1,
    },
    "electricity_co2": {
        "name": "Electricity CO2 Emissions Daily",
        "device_class": SensorDeviceClass.WEIGHT,
        "unit": UnitOfMass.KILOGRAMS,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:molecule-co2",
        "suggested_display_precision": 3,
    },
    "gas_co2": {
        "name": "Gas CO2 Emissions Daily",
        "device_class": SensorDeviceClass.WEIGHT,
        "unit": UnitOfMass.KILOGRAMS,
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:molecule-co2",
        "suggested_display_precision": 3,
    },
    "electricity_cost": {
        "name": "Electricity Cost Daily",
        "device_class": SensorDeviceClass.MONETARY,
        "unit": "EUR",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:currency-eur",
        "suggested_display_precision": 2,
    },
    "electricity_cost_consumption": {
        "name": "Electricity Cost Consumption Daily",
        "device_class": SensorDeviceClass.MONETARY,
        "unit": "EUR",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:cash",
        "suggested_display_precision": 2,
    },
    "electricity_cost_subscription": {
        "name": "Electricity Cost Subscription Daily",
        "device_class": SensorDeviceClass.MONETARY,
        "unit": "EUR",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:cash-clock",
        "suggested_display_precision": 2,
    },
    "gas_cost": {
        "name": "Gas Cost Daily",
        "device_class": SensorDeviceClass.MONETARY,
        "unit": "EUR",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:currency-eur",
        "suggested_display_precision": 2,
    },
    "gas_cost_consumption": {
        "name": "Gas Cost Consumption Daily",
        "device_class": SensorDeviceClass.MONETARY,
        "unit": "EUR",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:cash",
        "suggested_display_precision": 2,
    },
    "gas_cost_subscription": {
        "name": "Gas Cost Subscription Daily",
        "device_class": SensorDeviceClass.MONETARY,
        "unit": "EUR",
        "state_class": SensorStateClass.TOTAL,
        "icon": "mdi:cash-clock",
        "suggested_display_precision": 2,
    },
    "contract_provider": {
        "name": "Contract Provider",
        "device_class": None,
        "unit": None,
        "state_class": None,
        "icon": "mdi:handshake",
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    "contract_offer": {
        "name": "Contract Offer",
        "device_class": None,
        "unit": None,
        "state_class": None,
        "icon": "mdi:file-certificate",
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the HelloWatt sensor platform.

    Creates sensor entities for all configured PDLs and all sensor types
    (electricity, gas, temperature, costs, CO2).

    Args:
        hass: Home Assistant instance
        entry: Config entry for this integration
        async_add_entities: Callback to add entities to Home Assistant
    """
    coordinators: dict[str, HelloWattCoordinator] = hass.data[DOMAIN][entry.entry_id][
        "coordinators"
    ]

    entities: list[HelloWattSensor] = []

    # Create sensors for each PDL. __init__.async_setup_entry already called
    # async_config_entry_first_refresh, so coordinator.data is populated here.
    for pdl, coordinator in coordinators.items():
        available_keys = set(coordinator.data.keys()) if coordinator.data else set()

        # Add only sensor types that are present in the coordinator data
        for sensor_key, sensor_config in SENSOR_TYPES.items():
            if sensor_key in available_keys:
                entities.append(
                    HelloWattSensor(
                        coordinator,
                        pdl,
                        sensor_key,
                        sensor_config,
                    )
                )

    async_add_entities(entities)


class HelloWattSensor(CoordinatorEntity[HelloWattCoordinator], SensorEntity):
    """Representation of a HelloWatt Sensor.

    Each sensor represents a specific data point (electricity, gas, cost, etc.)
    for a particular PDL (Point de Livraison).

    Enhanced with Energy Dashboard compatibility:
    - suggested_display_precision for cleaner dashboard display
    - last_reset property for daily TOTAL sensors
    """

    def __init__(
        self,
        coordinator: HelloWattCoordinator,
        pdl: str,
        key_id: str,
        sensor_config: dict[str, Any],
    ) -> None:
        """Initialize the sensor.

        Args:
            coordinator: Data coordinator for this PDL
            pdl: Point de Livraison identifier
            key_id: Sensor key in coordinator data (e.g., 'electricity', 'gas')
            sensor_config: Complete sensor configuration dictionary
        """
        super().__init__(coordinator)
        self._pdl: str = pdl
        self._key_id: str = key_id
        self._attr_name: str = sensor_config["name"]
        # Ensure unique_id uses the PDL once so it starts with
        self._attr_unique_id: str = f"{DOMAIN}_{pdl}_{key_id}"
        self._attr_has_entity_name: bool = True
        self._attr_device_class: SensorDeviceClass | None = sensor_config[
            "device_class"
        ]
        self._attr_state_class: SensorStateClass | None = sensor_config["state_class"]
        self._attr_native_unit_of_measurement: str | None = sensor_config["unit"]
        self._attr_icon: str | None = sensor_config["icon"]
        if "entity_category" in sensor_config:
            self._attr_entity_category: EntityCategory | None = sensor_config[
                "entity_category"
            ]

        # Energy Dashboard enhancements
        if "suggested_display_precision" in sensor_config:
            self._attr_suggested_display_precision: int = sensor_config[
                "suggested_display_precision"
            ]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity.

        Groups all sensors for a PDL under a single device.
        """
        return DeviceInfo(
            identifiers={(DOMAIN, self._pdl)},
            name=f"HelloWatt {self._pdl}",
            manufacturer="HelloWatt",
            model="Energy Monitor",
            configuration_url="https://www.hellowatt.fr/mon-compte/",
        )

    @property
    def native_value(self) -> float | int | str | None:
        """Return the current state of the sensor.

        Returns:
            Sensor value from coordinator data, or None if not available
        """
        value = self.coordinator.data.get(self._key_id)
        # Filter out empty strings and invalid values
        if (
            value is None
            or value == ""
            or (isinstance(value, str) and not value.strip())
        ):
            return None
        return value if isinstance(value, (float, int, str)) else None

    @property
    def last_reset(self):
        """Return midnight of today for TOTAL sensors so HA doesn't compute negative deltas across daily resets."""
        if self._attr_state_class == SensorStateClass.TOTAL:
            from homeassistant.util import dt as dt_util

            now = dt_util.now()
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Entity is available only if:
        - Coordinator is available (connected to API)
        - Coordinator has data
        - The specific sensor key exists in coordinator data

        Returns:
            True if entity is available, False otherwise
        """
        return (
            super().available
            and self.coordinator.data is not None
            and self._key_id in self.coordinator.data
        )
