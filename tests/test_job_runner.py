"""Unit tests for JobRunner state machine, concurrency locking, and abort safety."""
import pytest
import asyncio
# pyrefly: ignore [missing-import]
from machine import Pin
from config import Config
from drivers.solenoid import SolenoidLock
from engine.actions import ActionRegistry, ActionExecutionContext
from engine.job_runner import JobRunner, JobState


@pytest.fixture
def runner_env():
    cfg = Config()
    sol = SolenoidLock(pin_num=23, active_high=True, default_pulse_ms=100)
    pins = {
        "buzzer": Pin(19, Pin.OUT, value=0),
        "solenoid": Pin(23, Pin.OUT, value=0)
    }
    status_updates = []
    async def status_cb(status_dict):
        status_updates.append(status_dict)

    ctx = ActionExecutionContext(config=cfg, pins=pins, solenoid=sol)
    reg = ActionRegistry()
    runner = JobRunner(action_registry=reg, context=ctx, status_callback=status_cb)
    return runner, ctx, status_updates


@pytest.mark.asyncio
async def test_run_named_routine(runner_env):
    runner, ctx, statuses = runner_env
    res = await runner.run_job("door_unlock")
    assert res["success"] is True
    assert runner.state == JobState.IDLE
    assert runner.is_busy is False
    assert ctx.solenoid.is_unlocked is False

    # Check status updates were emitted
    assert len(statuses) >= 2
    assert any(s.get("state") == "running" for s in statuses)


@pytest.mark.asyncio
async def test_concurrency_lock_rejects_second_job(runner_env):
    runner, ctx, _ = runner_env

    # Long job
    long_job = [{"action": "delay", "duration_ms": 250}]
    t1 = asyncio.create_task(runner.run_job(long_job, job_id="job_one"))

    await asyncio.sleep(0.05)
    assert runner.is_busy is True

    # Try 2nd job
    res2 = await runner.run_job("door_unlock", job_id="job_two")
    assert res2["success"] is False
    assert "busy running job: job_one" in res2["error"]

    await t1
    assert runner.is_busy is False


@pytest.mark.asyncio
async def test_emergency_abort_safety(runner_env):
    runner, ctx, _ = runner_env

    # Job with pulse
    job = [{"action": "pulse", "pin": "solenoid", "duration_ms": 500}]
    t = asyncio.create_task(runner.run_job(job, job_id="pulse_job"))

    await asyncio.sleep(0.05)
    assert runner.is_busy is True

    # Abort while running
    abort_res = await runner.abort(reason="user_emergency")
    assert abort_res["status"] == "aborted"
    assert runner.state == JobState.IDLE
    assert runner.is_busy is False
    # Verify hardware safe state
    assert ctx.solenoid.is_unlocked is False

    await t
