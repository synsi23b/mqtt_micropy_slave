"""Unit tests for Modular Action Registry."""
import pytest
# pyrefly: ignore [missing-import]
from machine import Pin, UART
from config import Config
from drivers.solenoid import SolenoidLock
from drivers.as608 import AS608, build_packet, CONFIRM_OK
from engine.actions import ActionRegistry, ActionExecutionContext


def make_ack_packet(confirm_code, return_data=b""):
    return build_packet(0x07, bytes([confirm_code]) + return_data)


@pytest.fixture
def test_context():
    cfg = Config()
    sol = SolenoidLock(pin_num=23, active_high=True, default_pulse_ms=200)
    uart = UART(port=2)
    sensor = AS608(uart)
    pins = {
        "buzzer": Pin(19, Pin.OUT, value=0),
        "led": Pin(2, Pin.OUT, value=0)
    }
    events = []
    async def event_cb(ev_type, details):
        events.append((ev_type, details))

    ctx = ActionExecutionContext(
        config=cfg,
        pins=pins,
        solenoid=sol,
        as608=sensor,
        event_cb=event_cb
    )
    ctx.recorded_events = events
    return ctx


@pytest.fixture
def registry():
    return ActionRegistry()


@pytest.mark.asyncio
async def test_action_digital_write(registry, test_context):
    step = {"action": "digital_write", "pin": "led", "state": 1}
    res = await registry.execute(step, test_context)
    assert res["state"] == 1
    assert test_context.pins["led"].value() == 1

    step2 = {"action": "digital_write", "pin": "led", "state": 0}
    await registry.execute(step2, test_context)
    assert test_context.pins["led"].value() == 0


@pytest.mark.asyncio
async def test_action_pulse_pin(registry, test_context):
    buzzer = test_context.pins["buzzer"]
    assert buzzer.value() == 0

    step = {"action": "pulse", "pin": "buzzer", "duration_ms": 50}
    res = await registry.execute(step, test_context)
    assert res["duration_ms"] == 50
    # Pin should have toggled high then back to low
    assert buzzer.value() == 0
    assert 1 in buzzer.history


@pytest.mark.asyncio
async def test_action_pulse_solenoid(registry, test_context):
    sol = test_context.solenoid
    assert sol.is_unlocked is False

    step = {"action": "pulse", "pin": "solenoid", "duration_ms": 60}
    res = await registry.execute(step, test_context)
    assert res["duration_ms"] == 60
    assert sol.is_unlocked is False


@pytest.mark.asyncio
async def test_action_delay(registry, test_context):
    step = {"action": "delay", "duration_ms": 30}
    res = await registry.execute(step, test_context)
    assert res["duration_ms"] == 30


@pytest.mark.asyncio
async def test_action_servo_set(registry, test_context):
    step = {"action": "servo_set", "pin": 25, "angle": 90}
    res = await registry.execute(step, test_context)
    assert res["angle"] == 90


@pytest.mark.asyncio
async def test_action_as608_search_event(registry, test_context):
    uart = test_context.as608.uart
    # Feed capture OK, img2tz OK, fast_search match (slot 5, score 120)
    uart.feed_rx(make_ack_packet(CONFIRM_OK))
    uart.feed_rx(make_ack_packet(CONFIRM_OK))
    match_data = (5).to_bytes(2, "big") + (120).to_bytes(2, "big")
    uart.feed_rx(make_ack_packet(CONFIRM_OK, match_data))

    step = {"action": "as608_search"}
    res = await registry.execute(step, test_context)
    assert res["found"] is True
    assert res["finger_id"] == 5
    assert res["confidence"] == 120

    # Ensure event was dispatched to callback
    assert len(test_context.recorded_events) == 1
    ev_type, payload = test_context.recorded_events[0]
    assert ev_type == "fingerprint_scanned"
    assert payload["finger_id"] == 5
