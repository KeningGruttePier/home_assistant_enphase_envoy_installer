from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    COORDINATOR,
    DOMAIN,
    NAME,
    READER,
    STORAGE_MODES,
    STORAGE_MODE_SELECT,
    DPEL_MODES,
    DPEL_MODE_SELECT,
    DPEL_PENDING,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data[COORDINATOR]
    name = data[NAME]
    reader = data[READER]
    # Shared with switch.py: holds the mode chosen here while DPEL is off,
    # so turning the switch on can use it.
    dpel_pending = data.setdefault(DPEL_PENDING, {})

    entities = []
    if (
        coordinator.data.get("batteries")
        and coordinator.data.get("storage_mode") is not None
    ):
        entity_name = f"{name} {STORAGE_MODE_SELECT.name}"
        entities.append(
            EnvoyStorageModeSelectEntity(
                STORAGE_MODE_SELECT,
                entity_name,
                name,
                config_entry.unique_id,
                None,
                coordinator,
                reader,
            )
        )

    # 'dpel_enabled' is only a key in coordinator.data on metered-with-CT
    # systems (structurally, regardless of whether its value has resolved
    # yet), and DPEL additionally requires an installer token. Both are
    # known right after the first coordinator refresh, so the select is
    # available from startup instead of only after DPEL has been
    # configured at least once. Unlike an earlier version of this entity,
    # it stays available even while DPEL is off, so a mode can be picked
    # ahead of time and used the next time the DPEL switch is turned on.
    if (
        "dpel_enabled" in coordinator.data
        and reader.token_type == "installer"
        and not reader.disable_installer_account_use
    ):
        entity_name = f"{name} {DPEL_MODE_SELECT.name}"
        entities.append(
            EnvoyDpelModeSelectEntity(
                DPEL_MODE_SELECT,
                entity_name,
                name,
                config_entry.unique_id,
                None,
                coordinator,
                reader,
                dpel_pending,
            )
        )
    async_add_entities(entities)


class EnvoySelectEntity(CoordinatorEntity, SelectEntity):
    def __init__(
        self,
        description,
        name,
        device_name,
        device_serial_number,
        serial_number,
        coordinator,
        reader,
    ):
        self.entity_description = description
        self._name = name
        self._serial_number = serial_number
        self._device_name = device_name
        self._device_serial_number = device_serial_number
        CoordinatorEntity.__init__(self, coordinator)
        self._is_on = False
        self.reader = reader

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id of the sensor."""
        if self._serial_number:
            return self._serial_number
        if self._device_serial_number:
            return f"{self._device_serial_number}_{self.entity_description.key}"

    @property
    def device_info(self) -> DeviceInfo or None:
        """Return the device_info of the device."""
        if not self._device_serial_number:
            return None

        model = self.coordinator.data.get("envoy_info", {}).get("model", "Standard")

        return DeviceInfo(
            identifiers={(DOMAIN, str(self._device_serial_number))},
            manufacturer="Enphase",
            model=f"Envoy-S {model}",
            name=self._device_name,
        )


class EnvoyStorageModeSelectEntity(EnvoySelectEntity):
    @property
    def current_option(self) -> str:
        """Return the status of the requested attribute."""
        return self.coordinator.data.get("storage_mode")

    @property
    def options(self) -> list:
        return STORAGE_MODES

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.reader.set_storage("mode", option)
        await self.coordinator.async_request_refresh()


class EnvoyDpelModeSelectEntity(EnvoySelectEntity):
    """Select for DPEL's Production/Export mode.

    Stays available even while DPEL is off, so a mode can be picked ahead
    of time; the DPEL switch reads that choice when turning DPEL on. While
    DPEL is already on, picking a mode here pushes it to the Envoy right
    away instead.
    """

    def __init__(
        self,
        description,
        name,
        device_name,
        device_serial_number,
        serial_number,
        coordinator,
        reader,
        pending,
    ):
        super().__init__(
            description,
            name,
            device_name,
            device_serial_number,
            serial_number,
            coordinator,
            reader,
        )
        # Shared with the DPEL switch entity.
        self._pending = pending

    @property
    def current_option(self) -> str:
        """Return the status of the requested attribute."""
        live_mode = self.coordinator.data.get("dpel_mode")
        if live_mode is not None:
            return live_mode
        if self._pending.get("mode") is not None:
            return self._pending["mode"]
        # Nothing known yet (DPEL never configured/enabled): default to
        # Export rather than showing an empty select.
        return "Export"

    @property
    def options(self) -> list:
        return DPEL_MODES

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        self._pending["mode"] = option
        if self.coordinator.data.get("dpel_enabled"):
            # DPEL is already live: push the new mode to the Envoy now.
            await self.reader.set_dpel_mode(option == "Export")
            await self.coordinator.async_request_refresh()
        else:
            # DPEL is off: just remember the choice for next time the
            # switch is turned on, without calling the Envoy (that would
            # enable DPEL as an unwanted side effect).
            self.async_write_ha_state()
