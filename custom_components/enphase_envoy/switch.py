from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    COORDINATOR,
    DOMAIN,
    NAME,
    READER,
    SWITCHES,
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
    # Shared with select.py: holds the mode chosen via the DPEL Mode select
    # while DPEL is off, so turning the switch on can use it even though
    # the Envoy itself has nothing to report for 'dpel_mode' while disabled.
    dpel_pending = data.setdefault(DPEL_PENDING, {})

    entities = []
    for switch_description in SWITCHES:
        if switch_description.key == "dpel_switch":
            # 'dpel_enabled' is only a key in coordinator.data on metered-
            # with-CT systems (structurally, regardless of whether its
            # value has resolved yet), and DPEL additionally requires an
            # installer token. Both are known right after the first
            # coordinator refresh, so the switch is available from
            # startup instead of only after DPEL has been
            # configured/enabled at least once.
            if (
                "dpel_enabled" in coordinator.data
                and reader.token_type == "installer"
                and not reader.disable_installer_account_use
            ):
                entity_name = f"{name} {switch_description.name}"
                entities.append(
                    EnvoyDpelSwitchEntity(
                        switch_description,
                        entity_name,
                        name,
                        config_entry.unique_id,
                        None,
                        coordinator,
                        reader,
                        dpel_pending,
                    )
                )
        elif switch_description.key.startswith("storage_"):
            if (
                coordinator.data.get("batteries")
                and coordinator.data.get(switch_description.key) is not None
            ):
                entity_name = f"{name} {switch_description.name}"
                entities.append(
                    EnvoyStorageSwitchEntity(
                        switch_description,
                        entity_name,
                        name,
                        config_entry.unique_id,
                        None,
                        coordinator,
                        reader,
                    )
                )
        else:
            if coordinator.data.get(switch_description.key) is not None:
                entity_name = f"{name} {switch_description.name}"
                entities.append(
                    EnvoySwitchEntity(
                        switch_description,
                        entity_name,
                        name,
                        config_entry.unique_id,
                        None,
                        coordinator,
                        reader,
                    )
                )
    async_add_entities(entities)


class EnvoySwitchEntity(CoordinatorEntity, SwitchEntity):
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

    @property
    def is_on(self) -> bool:
        """Return the status of the requested attribute."""
        return self.coordinator.data.get(self.entity_description.key)

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        set_func = getattr(self.reader, f"set_{self.entity_description.key}")
        await set_func(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        set_func = getattr(self.reader, f"set_{self.entity_description.key}")
        await set_func(False)
        await self.coordinator.async_request_refresh()


class EnvoyDpelSwitchEntity(EnvoySwitchEntity):
    """Switch to enable/disable Dynamic Power Export Limit.

    The Envoy only exposes the current enabled state, not a way to preview
    a toggle result, so we mark this as an assumed-state switch and drive
    it from the last-known coordinator values for immediate UI feedback.
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
        # Shared with the DPEL Mode select entity.
        self._pending = pending

    @property
    def assumed_state(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        """Return the status of the requested attribute."""
        return self.coordinator.data.get("dpel_enabled")

    async def async_turn_on(self, **kwargs):
        """Turn DPEL on, re-using the last-known limit/slew values and the
        mode chosen on the DPEL Mode select (falling back to the Envoy's
        last-known mode if nothing was ever selected while DPEL was off)."""
        mode = self._pending.get("mode") or self.coordinator.data.get("dpel_mode")
        await self.reader.enable_dpel(
            watt=self.coordinator.data.get("dpel_limit") or 0,
            slew=self.coordinator.data.get("dpel_slew_rate") or 100,
            export_limit=mode == "Export",
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn DPEL off."""
        await self.reader.disable_dpel()
        await self.coordinator.async_request_refresh()


class EnvoyStorageSwitchEntity(EnvoySwitchEntity):
    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        await self.reader.set_storage(self.entity_description.key[8:], True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        await self.reader.set_storage(self.entity_description.key[8:], False)
        await self.coordinator.async_request_refresh()
