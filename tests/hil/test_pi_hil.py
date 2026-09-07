"""
Raspberry Pi Hardware-in-the-Loop (HIL) Test Suite.
Run this script on a Raspberry Pi wired to the ESP32 to verify physical GPIO timings
and AS608 UART packet exchanges.
"""
import pytest
import time
import json
import os

BROKER_HOST = os.environ.get("MQTT_BROKER", "127.0.0.1")
DEVICE_ID = os.environ.get("DEVICE_ID", "esp32_door_slave_01")
SOLENOID_SENSE_PIN = int(os.environ.get("PI_SOLENOID_PIN", "17"))


def is_pi_hardware():
    """Detect if running on a real Raspberry Pi with GPIO access."""
    try:
        import gpiod
        return True
    except ImportError:
        try:
            import RPi.GPIO
            return True
        except ImportError:
            return False


pytestmark = pytest.mark.skipif(
    not is_pi_hardware(),
    reason="HIL tests require Raspberry Pi GPIO environment (gpiod or RPi.GPIO)"
)


def test_measure_solenoid_physical_pulse_width():
    """
    Triggers 'door_unlock' over MQTT, measures the physical HIGH duration
    on the Pi's input GPIO pin connected to ESP32's solenoid pin,
    and asserts timing accuracy.
    """
    import paho.mqtt.client as paho
    import gpiod

    chip = gpiod.Chip("gpiochip4") if os.path.exists("/dev/gpiochip4") else gpiod.Chip("gpiochip0")
    line = chip.get_line(SOLENOID_SENSE_PIN)
    line.request(consumer="hil_test", type=gpiod.LINE_REQ_DIR_IN)

    client = paho.Client(client_id="pi_hil_runner")
    client.connect(BROKER_HOST, 1883, 60)
    client.loop_start()

    # Trigger unlock command
    command_topic = f"slave/{DEVICE_ID}/job/run"
    client.publish(command_topic, json.dumps({"job": "door_unlock", "params": {"duration_ms": 1000}}))

    # Wait for line to go HIGH
    start_time = None
    timeout = time.time() + 3.0
    while time.time() < timeout:
        if line.get_value() == 1:
            start_time = time.perf_counter()
            break
        time.sleep(0.001)

    assert start_time is not None, "ESP32 Solenoid pin never went HIGH"

    # Wait for line to return LOW
    end_time = None
    timeout = time.time() + 2.0
    while time.time() < timeout:
        if line.get_value() == 0:
            end_time = time.perf_counter()
            break
        time.sleep(0.001)

    assert end_time is not None, "ESP32 Solenoid pin never returned to LOW (coil safety risk!)"

    pulse_duration_ms = (end_time - start_time) * 1000.0
    print(f"\n[HIL Result] Measured Solenoid Pulse Width: {pulse_duration_ms:.2f} ms")

    # Assert 1000ms ± 150ms physical tolerance
    assert 850.0 <= pulse_duration_ms <= 1150.0

    line.release()
    chip.close()
    client.loop_stop()
    client.disconnect()
