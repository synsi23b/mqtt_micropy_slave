"""Generic Analog ADC input driver with sample smoothing."""
try:
    from machine import Pin, ADC
except ImportError:
    class ADC:
        ATTN_11DB = 3
        def __init__(self, pin):
            self.pin = pin
            self._val = 2048
        def atten(self, att):
            pass
        def read(self):
            return self._val
        def read_u16(self):
            return self._val * 16

    class Pin:
        IN = 0
        def __init__(self, pin, mode=0):
            self.pin = pin


class AnalogInput:
    """Reads analog sensor values (e.g. photoresistors, voltage dividers, pot)."""

    def __init__(self, pin_num, report_interval_s=60, ha_device_class="voltage", samples=4):
        self.pin_num = pin_num
        self.report_interval_s = report_interval_s
        self.ha_device_class = ha_device_class
        self.samples = samples

        try:
            self.adc = ADC(Pin(pin_num, Pin.IN))
            if hasattr(self.adc, "atten") and hasattr(ADC, "ATTN_11DB"):
                self.adc.atten(ADC.ATTN_11DB)
        except Exception:
            self.adc = ADC(Pin(pin_num))

        self._last_val = None

    def read_raw(self):
        """Read averaged 12-bit ADC value (0 - 4095)."""
        total = 0
        for _ in range(self.samples):
            if hasattr(self.adc, "read"):
                total += self.adc.read()
            elif hasattr(self.adc, "read_u16"):
                total += self.adc.read_u16() >> 4
            else:
                total += 2048
        val = int(total / self.samples)
        self._last_val = val
        return val

    def read_voltage(self, max_voltage=3.3):
        """Read estimated voltage based on 12-bit range."""
        raw = self.read_raw()
        return round((raw / 4095.0) * max_voltage, 2)
