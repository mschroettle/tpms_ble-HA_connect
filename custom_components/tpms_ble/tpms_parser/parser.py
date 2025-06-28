from __future__ import annotations
from datetime import datetime
import logging
import re
import asyncio
from typing import Optional

from bluetooth_data_tools import short_address
from bluetooth_sensor_state_data import BluetoothData
from home_assistant_bluetooth import BluetoothServiceInfo, BluetoothCharacteristic
from sensor_state_data.enum import StrEnum

_LOGGER = logging.getLogger(__name__)


class TPMSSensor(StrEnum):
    PRESSURE = "pressure"
    TEMPERATURE = "temperature"
    BATTERY = "battery"
    TIMESTAMP = "timestamp"


class TPMSBinarySensor(StrEnum):
    ALARM = "alarm"


class TPMSBluetoothDeviceData(BluetoothData):
    TARGET_NAMES = ["TYREDOG", "JDY-08", "Realtek", "RB8762"]
    SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
    WRITE_CHARACTERISTIC_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
    NOTIFY_CHARACTERISTIC_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

    def __init__(self):
        super().__init__()
        self.notify_char: Optional[BluetoothCharacteristic] = None
        self.write_char: Optional[BluetoothCharacteristic] = None

    async def _start_update(self, service_info: BluetoothServiceInfo) -> None:
        _LOGGER.debug("Parsing TPMS UART BLE advertisement: %s", service_info)

        if service_info.name not in self.TARGET_NAMES:
            return

        if self.SERVICE_UUID not in service_info.service_uuids:
            return

        self.set_device_manufacturer("TPMS")
        self.set_device_name(service_info.name)
        self.set_title(service_info.name)

        for char in service_info.characteristics:
            if char.uuid == self.NOTIFY_CHARACTERISTIC_UUID:
                self.notify_char = char
            elif char.uuid == self.WRITE_CHARACTERISTIC_UUID:
                self.write_char = char

        if self.notify_char and self.write_char:
            await self._enable_notifications_and_send_command()

    async def _enable_notifications_and_send_command(self):
        await asyncio.sleep(0.1)
        await self.notify_char.start_notify(self._on_data_received)

        command = "$A0240138#"
        bytes_to_send = command.encode("utf-8")
        await self.write_char.write_value(bytes_to_send, response=False)
        await asyncio.sleep(0.3)

    def _on_data_received(self, data: bytes):
        try:
            data_str = data.decode("utf-8")
        except Exception as e:
            _LOGGER.warning("Failed to decode: %s", e)
            return

        if len(data_str) == 16:
            parsed = self._handle_16_byte_data(data_str)
            if parsed:
                self._update_sensors(**parsed)

    def _handle_16_byte_data(self, data: str) -> Optional[dict]:
        if not data.startswith("$A0") or not data.endswith("#"):
            return None

        def byte2flag(byte1, byte0, bit):
            byte0 = (byte1 * 16) + byte0
            for _ in range(bit + 1):
                val = byte0 % 2
                byte0 = byte0 // 2
            return val == 1

        try:
            sensor_no = int(data[6], 16) + 1
            flag_hi = int(data[7], 16)
            flag_lo = int(data[8], 16)

            battery_low = byte2flag(flag_hi, flag_lo, 1)
            rx_data_fresh = byte2flag(flag_hi, flag_lo, 7)
            half_point = byte2flag(flag_hi, flag_lo, 0)

            if not rx_data_fresh:
                return None

            pressure_hex = data[9:11] + data[11]
            temp_hex = data[11:13]

            pressure = int(pressure_hex, 16)
            if half_point:
                pressure += 0.5

            temperature = int(temp_hex, 16) - 40

            return {
                "pressure": pressure,
                "temperature": temperature,
                "battery": 20 if battery_low else 100,
                "sensor_no": sensor_no,
            }
        except Exception as e:
            _LOGGER.warning("Error parsing 16-byte data: %s", e)
            return None

    def _update_sensors(self, pressure, temperature, battery, sensor_no):
        name = f"TPMS Sensor {sensor_no}"
        self.set_device_type(name)
        self.set_title(name)

        self.update_sensor(
            key=str(TPMSSensor.PRESSURE),
            native_unit_of_measurement="psi",
            native_value=pressure,
            name="Pressure",
        )
        self.update_sensor(
            key=str(TPMSSensor.TEMPERATURE),
            native_unit_of_measurement="°C",
            native_value=temperature,
            name="Temperature",
        )
        self.update_sensor(
            key=str(TPMSSensor.BATTERY),
            native_unit_of_measurement="%",
            native_value=battery,
            name="Battery",
        )
        self.update_sensor(
            key=str(TPMSSensor.TIMESTAMP),
            native_unit_of_measurement=None,
            native_value=datetime.now().astimezone(),
            name="Last Update",
        )
