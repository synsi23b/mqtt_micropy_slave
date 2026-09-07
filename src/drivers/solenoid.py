"""Solenoid driver with hardware safety timeouts to prevent coil overheating."""
try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    from machine import Pin
except ImportError:
    # Fallback mock for desktop/testing if not imported via conftest
    class Pin:
        OUT = 1
        IN = 0
        def __init__(self, pin, mode=1, value=0):
            self._pin = pin
            self._val = value
        def value(self, val=None):
            if val is not None:
                self._val = val
            return self._val


class SolenoidLock:
    """Failsafe Solenoid Door Lock controller."""

    def __init__(self, pin_num=23, active_high=True, default_pulse_ms=3000, max_pulse_ms=10000):
        self.pin_num = pin_num
        self.active_high = active_high
        self.default_pulse_ms = default_pulse_ms
        self.max_pulse_ms = max_pulse_ms
        self._active_val = 1 if active_high else 0
        self._idle_val = 0 if active_high else 1

        self.pin = Pin(pin_num, Pin.OUT)
        self.pin.value(self._idle_val)
        self._is_active = False
        self._active_task = None

    @property
    def is_unlocked(self):
        return self._is_active

    def failsafe_reset(self):
        """Immediately de-energize coil to safe idle state."""
        self._is_active = False
        self.pin.value(self._idle_val)
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        self._active_task = None

    def lock(self):
        """Lock immediately (de-energize)."""
        self.failsafe_reset()

    async def unlock(self, duration_ms=None):
        """Energize the solenoid for a specified duration, then automatically de-energize."""
        if duration_ms is None:
            duration_ms = self.default_pulse_ms
        
        # Clamp to max safety limit
        if duration_ms > self.max_pulse_ms:
            duration_ms = self.max_pulse_ms
        if duration_ms < 50:
            duration_ms = 50

        # Cancel any ongoing unlock task
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()

        self._active_task = asyncio.current_task()
        try:
            self._is_active = True
            self.pin.value(self._active_val)
            await asyncio.sleep_ms(duration_ms)
        finally:
            self.pin.value(self._idle_val)
            self._is_active = False
            self._active_task = None
