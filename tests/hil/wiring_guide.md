# Raspberry Pi <-> ESP32 Hardware-in-the-Loop (HIL) Wiring Guide

This guide describes how to connect a Raspberry Pi (acting as the test fixture) to the ESP32 under test.

---

## 1. Pin Mapping & Interconnects

Both the Raspberry Pi and ESP32 operate at **3.3V logic levels**, meaning their GPIOs and UARTs can be connected directly without level shifters.

| Signal | ESP32 Pin | Raspberry Pi Pin (Header) | Description |
|--------|-----------|---------------------------|-------------|
| **Common GND** | GND | Pin 6 / 9 / 14 / 20 / 39 (GND) | Essential shared ground reference |
| **Solenoid Sense** | GPIO 23 (Out) | GPIO 17 (Pin 11, Input) | Pi measures actual physical pulse width & timing |
| **Buzzer Sense** | GPIO 19 (Out) | GPIO 27 (Pin 13, Input) | Pi asserts chirp duration & patterns |
| **UART RX** | GPIO 16 (RX2) | GPIO 14 / TXD0 (Pin 8, UART TX) | Pi emulates AS608 response stream |
| **UART TX** | GPIO 17 (TX2) | GPIO 15 / RXD0 (Pin 10, UART RX) | Pi captures commands sent by ESP32 |
| **WAK Sense** | GPIO 18 (In) | GPIO 22 (Pin 15, Output) | Pi simulates AS608 touch interrupt |

---

## 2. Electrical Considerations

> [!CAUTION]
> If testing with a real 12V / 24V solenoid valve/lock in production:
> - The ESP32 GPIO 23 drives a MOSFET (e.g. IRLZ44N) or Relay module, NOT the solenoid directly.
> - Always include a flyback diode (e.g. 1N4007) antiparallel across the solenoid coil to clamp inductive flyback voltage spikes.
> - In HIL testing, connect the Raspberry Pi GPIO 17 directly to the ESP32's 3.3V gate signal before the MOSFET or relay driver.

---

## 3. Running HIL Tests from the Raspberry Pi

1. Enable UART on the Raspberry Pi:
   ```bash
   sudo raspi-config
   # Interface Options -> Serial Port -> Shell: NO, Hardware: YES
   ```
2. Install test dependencies on the Pi:
   ```bash
   pip install pytest pytest-asyncio paho-mqtt pyserial gpiod
   ```
3. Run the HIL suite over SSH:
   ```bash
   pytest tests/hil/test_pi_hil.py -v --broker 192.168.1.100 --device-id esp32_door_slave_01
   ```
