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


@pytest.mark.asyncio
async def test_action_lock_and_unlock(registry, test_context):
    sol = test_context.solenoid
    assert sol.is_unlocked is False

    # Unlock for 50ms
    res_unlock = await registry.execute({"action": "unlock", "duration_ms": 50}, test_context)
    assert res_unlock["action"] == "unlock"
    assert sol.is_unlocked is False

    # Lock immediately
    res_lock = await registry.execute({"action": "lock"}, test_context)
    assert res_lock["action"] == "lock"
    assert sol.is_unlocked is False


@pytest.mark.asyncio
async def test_action_aliases(registry, test_context):
    # 'write' alias for 'digital_write'
    await registry.execute({"action": "write", "pin": "led", "state": 1}, test_context)
    assert test_context.pins["led"].value() == 1

    # 'sleep' alias for 'delay'
    res_sleep = await registry.execute({"action": "sleep", "duration_ms": 20}, test_context)
    assert res_sleep["duration_ms"] == 20

    # 'servo' alias for 'servo_set'
    res_servo = await registry.execute({"action": "servo", "pin": 25, "angle": 45}, test_context)
    assert res_servo["angle"] == 45

    # 'pwm' alias for 'pwm_write'
    res_pwm = await registry.execute({"action": "pwm", "pin": 18, "freq": 2000, "duty_u16": 1000}, test_context)
    assert res_pwm["freq"] == 2000


@pytest.mark.asyncio
async def test_custom_action_registration(registry, test_context):
    # Test instance decorator
    @registry.action("custom_beep")
    async def handle_beep(step, ctx):
        buzzer = ctx.resolve_target("buzzer")
        buzzer.value(1)
        return {"action": "custom_beep", "status": "beeped"}

    assert registry.has_action("custom_beep")
    assert "custom_beep" in registry.registered_actions

    res = await registry.execute({"action": "custom_beep"}, test_context)
    assert res["status"] == "beeped"
    assert test_context.pins["buzzer"].value() == 1


@pytest.mark.asyncio
async def test_global_custom_action_registration(test_context):
    @ActionRegistry.custom_action("global_custom_action")
    async def handle_global(step, ctx):
        return {"action": "global_custom_action", "ok": True}

    # New registry should automatically have the global custom action
    new_reg = ActionRegistry()
    assert new_reg.has_action("global_custom_action")
    res = await new_reg.execute({"action": "global_custom_action"}, test_context)
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_as608_extra_actions(registry, test_context):
    uart = test_context.as608.uart

    # as608_led
    uart.feed_rx(make_ack_packet(CONFIRM_OK))
    res_led = await registry.execute({"action": "as608_led", "color": 2, "mode": 1}, test_context)
    assert res_led["ok"] is True

    # as608_empty
    uart.feed_rx(make_ack_packet(CONFIRM_OK))
    res_empty = await registry.execute({"action": "as608_empty"}, test_context)
    assert res_empty["ok"] is True

    # as608_delete alias delete_slot
    uart.feed_rx(make_ack_packet(CONFIRM_OK))
    res_del = await registry.execute({"action": "delete_slot", "slot_id": 3}, test_context)
    assert res_del["ok"] is True
    assert res_del["slot_id"] == 3
