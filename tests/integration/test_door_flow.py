"""End-to-End simulation of fingerprint scan -> auth decision -> door unlock execution."""
import pytest
import asyncio
from machine import Pin, UART
from config import Config
from drivers.solenoid import SolenoidLock
from drivers.as608 import AS608, build_packet, CONFIRM_OK
from engine.actions import ActionRegistry, ActionExecutionContext
from engine.job_runner import JobRunner


def make_ack_packet(confirm_code, return_data=b""):
    return build_packet(0x07, bytes([confirm_code]) + return_data)


@pytest.mark.asyncio
async def test_complete_fingerprint_to_unlock_flow():
    # Setup hardware and engine
    cfg = Config()
    sol = SolenoidLock(pin_num=23, active_high=True, default_pulse_ms=80)
    uart = UART(port=2)
    sensor = AS608(uart)
    pins = {
        "buzzer": Pin(19, Pin.OUT, value=0),
        "solenoid": Pin(23, Pin.OUT, value=0)
    }

    published_events = []
    status_updates = []

    async def event_cb(ev_type, details):
        published_events.append((ev_type, details))

    async def status_cb(status_dict):
        status_updates.append(status_dict)

    ctx = ActionExecutionContext(
        config=cfg,
        pins=pins,
        solenoid=sol,
        as608=sensor,
        event_cb=event_cb
    )
    reg = ActionRegistry()
    runner = JobRunner(action_registry=reg, context=ctx, status_callback=status_cb)

    # 1. Simulate user placing finger (Slot 42, Score 150)
    uart.feed_rx(make_ack_packet(CONFIRM_OK))
    uart.feed_rx(make_ack_packet(CONFIRM_OK))
    match_data = (42).to_bytes(2, "big") + (150).to_bytes(2, "big")
    uart.feed_rx(make_ack_packet(CONFIRM_OK, match_data))

    # Slave executes as608_search action
    search_res = await reg.execute({"action": "as608_search"}, ctx)
    assert search_res["found"] is True
    assert search_res["finger_id"] == 42
    assert len(published_events) == 1

    # 2. Simulated Auth App logic
    ev_type, payload = published_events[0]
    assert ev_type == "fingerprint_scanned"
    finger_id = payload["finger_id"]

    # Auth App database lookup:
    mock_db = {
        42: {"user": "Alice", "authorized": True},
        99: {"user": "Bob", "authorized": False}
    }
    user_record = mock_db.get(finger_id)
    assert user_record is not None
    assert user_record["user"] == "Alice"
    assert user_record["authorized"] is True

    # 3. Auth App dispatches 'door_unlock' job to slave
    job_res = await runner.run_job("door_unlock", job_id="auth_unlock_alice")
    assert job_res["success"] is True

    # 4. Confirm solenoid executed pulse and is now locked again
    assert sol.is_unlocked is False
    assert any(s.get("state") == "completed" for s in status_updates)
