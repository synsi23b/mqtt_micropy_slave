"""Servo motor PWM driver for MicroPython."""
try:
    # pyrefly: ignore [missing-import]
    from machine import Pin, PWM
except ImportError:
    class PWM:
        def __init__(self, pin, freq=50, duty=0, duty_u16=0):
            self.pin = pin
            self._freq = freq
            self._duty_u16 = duty_u16
        def freq(self, f=None):
            if f is not None:
                self._freq = f
            return self._freq
        def duty_u16(self, d=None):
            if d is not None:
                self._duty_u16 = d
            return self._duty_u16
        def deinit(self):
            pass

    class Pin:
        OUT = 1
        def __init__(self, pin, mode=1):
            self.pin = pin


class Servo:
    """Standard 50Hz PWM Servo Controller (500us to 2500us pulse width)."""

    def __init__(self, pin_num, min_us=500, max_us=2500, max_angle=180):
        self.pin_num = pin_num
        self.min_us = min_us
        self.max_us = max_us
        self.max_angle = max_angle
        self.pwm = PWM(Pin(pin_num, Pin.OUT), freq=50)
        self._current_angle = None

    def set_pulse_us(self, us):
        """Set raw pulse duration in microseconds (500 to 2500)."""
        us = max(self.min_us, min(self.max_us, us))
        # 50Hz period = 20,000us. duty_u16 range is 0 - 65535.
        duty = int((us / 20000.0) * 65535)
        self.pwm.duty_u16(duty)

    def set_angle(self, angle):
        """Set angle in degrees (0 to max_angle)."""
        angle = max(0, min(self.max_angle, angle))
        self._current_angle = angle
        us = self.min_us + (angle / self.max_angle) * (self.max_us - self.min_us)
        self.set_pulse_us(us)

    def deinit(self):
        """Release PWM pin."""
        self.pwm.deinit()
