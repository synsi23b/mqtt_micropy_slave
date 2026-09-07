"""Async Wi-Fi connection manager with exponential backoff reconnects."""
try:
    # pyrefly: ignore [missing-import]
    import uasyncio as asyncio
except ImportError:
    import asyncio


class WiFiManager:
    """Manages ESP32 Wi-Fi station mode connection lifecycle."""

    def __init__(self, ssid="", password="", connect_timeout_s=20, max_retries=5):
        self.ssid = ssid
        self.password = password
        self.connect_timeout_s = connect_timeout_s
        self.max_retries = max_retries
        self._wlan = None
        self._is_connected = False
        self._init_interface()

    def _init_interface(self):
        try:
            # pyrefly: ignore [missing-import]
            import network
            self._wlan = network.WLAN(network.STA_IF)
            self._wlan.active(True)
        except Exception:
            self._wlan = None

    def is_connected(self):
        if self._wlan:
            return self._wlan.isconnected()
        return self._is_connected

    async def connect(self):
        """Attempt to connect to Wi-Fi station with timeout."""
        if not self.ssid:
            print("[WiFi] No SSID configured, skipping Wi-Fi connection")
            return False

        if not self._wlan:
            # Desktop/simulator mode
            self._is_connected = True
            return True

        if self._wlan.isconnected():
            return True

        print("[WiFi] Connecting to '{}'...".format(self.ssid))
        self._wlan.connect(self.ssid, self.password)

        elapsed = 0
        while not self._wlan.isconnected() and elapsed < self.connect_timeout_s:
            await asyncio.sleep_ms(500)
            elapsed += 0.5

        if self._wlan.isconnected():
            print("[WiFi] Connected! IP:", self._wlan.ifconfig()[0])
            self._is_connected = True
            return True
        else:
            print("[WiFi] Connection timed out after {}s".format(self.connect_timeout_s))
            return False

    async def maintain_connection(self):
        """Background supervisor task that continuously monitors Wi-Fi and reconnects with backoff."""
        backoff_s = 2
        while True:
            if not self.is_connected():
                print("[WiFi] Connection lost. Reconnecting...")
                success = await self.connect()
                if success:
                    backoff_s = 2
                else:
                    backoff_s = min(backoff_s * 2, 60)
                    print("[WiFi] Reconnect failed. Backing off for {}s...".format(backoff_s))
                    await asyncio.sleep(backoff_s)
            else:
                await asyncio.sleep(5)
