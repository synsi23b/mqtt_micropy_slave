"""Board boot initialization for ESP32 MicroPython slave."""
import gc
try:
    # pyrefly: ignore [missing-import]
    import esp
    esp.osdebug(None)
except Exception:
    pass

# Collect garbage at startup
gc.collect()
gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())
