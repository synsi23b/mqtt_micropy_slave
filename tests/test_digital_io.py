"""Unit tests for DigitalInput and DigitalOutput drivers."""
import pytest
from machine import Pin
from drivers.digital_io import DigitalInput, DigitalOutput


@pytest.mark.asyncio
async def test_digital_input_inversion_and_change_detection():
    # Pin starts at 0
    inp = DigitalInput(pin_num=34, invert=True, debounce_ms=0, report_changes=True)
    # Since invert=True, physical 0 -> read 1
    assert inp.read() == 1

    # Simulate appliance LED turning off (physical pin goes HIGH)
    inp.pin.value(1)
    changed, state = await inp.check_change()
    assert changed is True
    assert state == 0  # Inverted

    # Check without change
    changed, state = await inp.check_change()
    assert changed is False
    assert state == 0


@pytest.mark.asyncio
async def test_digital_output_pulse():
    out = DigitalOutput(pin_num=19, active_high=True, initial_state=0)
    assert out.pin.value() == 0

    await out.pulse(duration_ms=40)
    assert out.pin.value() == 0
    assert 1 in out.pin.history
