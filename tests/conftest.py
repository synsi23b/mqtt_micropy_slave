"""Pytest configuration and MicroPython desktop hardware mocks."""
import sys
import types
import pytest
import asyncio

# Ensure src/ is in python path
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class MockPin:
    OUT = 1
    IN = 0
    PULL_UP = 1
    PULL_DOWN = 2

    def __init__(self, pin, mode=1, pull=-1, value=0):
        self.pin = pin
        self.mode = mode
        self.pull = pull
        self._val = value
        self.history = [value]

    def value(self, val=None):
        if val is not None:
            self._val = int(val)
            self.history.append(self._val)
        return self._val

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)


class MockPWM:
    def __init__(self, pin, freq=50, duty_u16=0):
        self.pin = pin
        self._freq = freq
        self._duty_u16 = duty_u16
        self.deinitialized = False

    def freq(self, f=None):
        if f is not None:
            self._freq = f
        return self._freq

    def duty_u16(self, d=None):
        if d is not None:
            self._duty_u16 = d
        return self._duty_u16

    def deinit(self):
        self.deinitialized = True


class MockUART:
    """Mock UART supporting read/write buffers for AS608 testing."""

    def __init__(self, port=2, baudrate=57600, tx=None, rx=None):
        self.port = port
        self.baudrate = baudrate
        self.tx = tx
        self.rx = rx
        self.written_bytes = bytearray()
        self.rx_queue = bytearray()

    def write(self, data):
        self.written_bytes.extend(data)
        return len(data)

    def read(self, n=None):
        if n is None or n >= len(self.rx_queue):
            res = bytes(self.rx_queue)
            self.rx_queue.clear()
            return res
        res = bytes(self.rx_queue[:n])
        del self.rx_queue[:n]
        return res

    def any(self):
        return len(self.rx_queue)

    def feed_rx(self, data):
        """Simulate bytes arriving over UART from peripheral."""
        self.rx_queue.extend(data)


# Install mock 'machine' module if not on MicroPython
if "machine" not in sys.modules:
    mock_machine = types.ModuleType("machine")
    mock_machine.Pin = MockPin
    mock_machine.PWM = MockPWM
    mock_machine.UART = MockUART
    sys.modules["machine"] = mock_machine

# Install mock 'uasyncio' with helper aliases
if "uasyncio" not in sys.modules:
    mock_uasyncio = types.ModuleType("uasyncio")
    for attr in dir(asyncio):
        setattr(mock_uasyncio, attr, getattr(asyncio, attr))

    async def sleep_ms(ms):
        await asyncio.sleep(ms / 1000.0)

    async def wait_for_ms(coro, ms):
        return await asyncio.wait_for(coro, ms / 1000.0)

    mock_uasyncio.sleep_ms = sleep_ms
    mock_uasyncio.wait_for_ms = wait_for_ms
    sys.modules["uasyncio"] = mock_uasyncio
