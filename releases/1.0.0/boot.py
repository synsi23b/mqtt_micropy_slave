"""Board boot initialization for ESP32 MicroPython slave."""
import gc
import esp

# Disable verbose OS debug output
try:
    esp.osdebug(None)
except Exception:
    pass

# Collect garbage at startup
gc.collect()
gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())
