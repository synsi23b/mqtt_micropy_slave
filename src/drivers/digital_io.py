"""Generic Digital I/O driver with inversion, debouncing, and change detection."""
try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    from machine import Pin
except ImportError:
    class Pin:
        OUT = 1
        IN = 0
        PULL_UP = 1
        PULL_DOWN = 2
        def __init__(self, pin, mode=1, value=0, pull=-1):
            self.pin = pin
            self._val = value
        def value(self, val=None):
            if val is not None:
                self._val = val
            return self._val


class DigitalInput:
    """Reads digital signals (e.g. status LEDs, reed switches, pushbuttons)."""

    def __init__(self, pin_num, pull="none", invert=False, debounce_ms=50, report_changes=False, ha_device_class=None):
        self.pin_num = pin_num
        self.invert = invert
        self.debounce_ms = debounce_ms
        self.report_changes = report_changes
        self.ha_device_class = ha_device_class

        pull_val = -1
        if pull == "up" and hasattr(Pin, "PULL_UP"):
            pull_val = Pin.PULL_UP
        elif pull == "down" and hasattr(Pin, "PULL_DOWN"):
            pull_val = Pin.PULL_DOWN

        try:
            self.pin = Pin(pin_num, Pin.IN, pull_val)
        except TypeError:
            self.pin = Pin(pin_num, Pin.IN)

        self._last_state = self.read()

    def read(self):
        """Read instantaneous logic state, applying inversion if configured."""
        val = self.pin.value()
        if self.invert:
            val = 0 if val else 1
        return val

    async def check_change(self):
        """Check if input state has changed, with debounce filtering."""
        current = self.read()
        if current != self._last_state:
            if self.debounce_ms > 0:
                await asyncio.sleep_ms(self.debounce_ms)
                current = self.read()
            if current != self._last_state:
                self._last_state = current
                return True, current
        return False, self._last_state


class DigitalOutput:
    """Controls discrete outputs (e.g. status LEDs, buzzers, relays)."""

    def __init__(self, pin_num, active_high=True, initial_state=0):
        self.pin_num = pin_num
        self.active_high = active_high
        self._active_val = 1 if active_high else 0
        self._idle_val = 0 if active_high else 1

        self.pin = Pin(pin_num, Pin.OUT)
        self.write(initial_state)

    def write(self, state):
        """Set output state (1/True = active, 0/False = idle)."""
        val = self._active_val if state else self._idle_val
        self.pin.value(val)

    async def pulse(self, duration_ms):
        """Pulse output to active state for duration_ms, then return to idle."""
        self.write(1)
        try:
            await asyncio.sleep_ms(duration_ms)
        finally:
            self.write(0)
