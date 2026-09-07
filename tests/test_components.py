"""Unit tests for Dynamic ComponentManager across various device profiles."""
import pytest
# pyrefly: ignore [missing-import]
from machine import Pin, UART
from engine.components import ComponentManager


def test_door_profile():
    """Verify component manager instantiates door solenoid, buzzer, and AS608."""
    cfg = {
        "door_bolt": {
            "type": "solenoid",
            "pin": 23,
            "active_high": True,
            "default_pulse_ms": 2000
        },
        "door_buzzer": {
            "type": "digital_out",
            "pin": 19,
            "active_high": True
        },
        "fingerprint": {
            "type": "as608",
            "uart_id": 2,
            "tx_pin": 17,
            "rx_pin": 16
        }
    }
    mgr = ComponentManager(cfg)
    assert mgr.get("door_bolt") is not None
    assert mgr.get("door_buzzer") is not None
    assert mgr.get("fingerprint") is not None

    solenoids = mgr.get_by_type("solenoid")
    assert len(solenoids) == 1
    assert solenoids[0][0] == "door_bolt"


def test_switchbot_profile():
    """Verify component manager instantiates servo for mechanical pressing."""
    cfg = {
        "coffee_button_presser": {
            "type": "servo",
            "pin": 25,
            "min_us": 500,
            "max_us": 2500,
            "max_angle": 180
        }
    }
    mgr = ComponentManager(cfg)
    servo = mgr.get("coffee_button_presser")
    assert servo is not None
    assert hasattr(servo, "set_angle")


def test_dumb_appliance_led_reader_profile():
    """Verify component manager instantiates digital_in with change reporting for dumb appliance status LEDs."""
    cfg = {
        "washer_run_led": {
            "type": "digital_in",
            "pin": 34,
            "pull": "up",
            "invert": True,
            "report_changes": True,
            "ha_device_class": "running"
        },
        "solar_voltage": {
            "type": "analog_in",
            "pin": 36,
            "report_interval_s": 30
        }
    }
    mgr = ComponentManager(cfg)
    reporting_inputs = mgr.get_reporting_inputs()
    assert len(reporting_inputs) == 1
    assert reporting_inputs[0][0] == "washer_run_led"

    analogs = mgr.get_reporting_analogs()
    assert len(analogs) == 1
    assert analogs[0][0] == "solar_voltage"


def test_failsafe_all():
    """Verify failsafe_all de-energizes all active solenoids and outputs."""
    cfg = {
        "lock1": {"type": "solenoid", "pin": 23},
        "led1": {"type": "digital_out", "pin": 2, "initial_state": 1}
    }
    mgr = ComponentManager(cfg)
    led = mgr.get("led1")
    assert led.pin.value() == 1

    mgr.failsafe_all()
    assert led.pin.value() == 0
