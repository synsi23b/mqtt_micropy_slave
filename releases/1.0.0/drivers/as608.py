"""MicroPython async driver for AS608 Optical Fingerprint Sensor over UART."""
try:
    # pyrefly: ignore [missing-import]
    import uasyncio as asyncio
except ImportError:
    import asyncio

import time

if hasattr(time, "ticks_ms"):
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
else:
    def ticks_ms():
        return int(time.time() * 1000)
    def ticks_diff(a, b):
        return a - b


# Packet constants
HEADER = b"\xef\x01"
DEFAULT_ADDRESS = b"\xff\xff\xff\xff"

# Packet Identifiers
PID_COMMAND = 0x01
PID_DATA = 0x02
PID_ACK = 0x07
PID_END_DATA = 0x08

# Instructions
CMD_GEN_IMG = 0x01
CMD_IMG_2_TZ = 0x02
CMD_MATCH = 0x03
CMD_SEARCH = 0x04
CMD_REG_MODEL = 0x05
CMD_STORE = 0x06
CMD_LOAD_CHAR = 0x07
CMD_UP_CHAR = 0x08
CMD_DOWN_CHAR = 0x09
CMD_DEL_CHAR = 0x0C
CMD_EMPTY = 0x0D
CMD_AURA_LED = 0x0E
CMD_VFY_PWD = 0x13
CMD_READ_SYS_PARA = 0x0F
CMD_FAST_SEARCH = 0x1B

# Confirmation Codes
CONFIRM_OK = 0x00
CONFIRM_ERR_RECEIVE = 0x01
CONFIRM_NO_FINGER = 0x02
CONFIRM_FAIL_GEN_IMG = 0x03
CONFIRM_OVERLY_DISORDER = 0x06
CONFIRM_LACK_CHARACTER = 0x07
CONFIRM_NOT_MATCH = 0x08
CONFIRM_NOT_FOUND = 0x09
CONFIRM_FAIL_COMBINE = 0x0A
CONFIRM_BAD_PAGE_ID = 0x0B
CONFIRM_WRONG_PASSWORD = 0x13


def build_packet(pid, content, address=DEFAULT_ADDRESS):
    """Construct an AS608 packet with 16-bit checksum."""
    length = len(content) + 2
    length_bytes = bytes([(length >> 8) & 0xFF, length & 0xFF])
    packet_pre = bytes([pid]) + length_bytes + content
    checksum = (pid + (length >> 8) + (length & 0xFF) + sum(content)) & 0xFFFF
    checksum_bytes = bytes([(checksum >> 8) & 0xFF, checksum & 0xFF])
    return HEADER + address + packet_pre + checksum_bytes


class AS608:
    """Async AS608 Optical Fingerprint Reader Controller."""

    def __init__(self, uart, address=0xFFFFFFFF, password=0):
        self.uart = uart
        self.address = address.to_bytes(4, "big") if isinstance(address, int) else address
        self.password = password

    async def _drain_rx(self):
        """Discard unread bytes in UART RX buffer."""
        try:
            while getattr(self.uart, "any", lambda: False)():
                if hasattr(self.uart, "read"):
                    self.uart.read(self.uart.any())
                await asyncio.sleep_ms(2)
        except Exception:
            pass

    async def _read_exact(self, count, timeout_ms=1000):
        """Read exactly `count` bytes from UART with timeout."""
        buf = bytearray()
        start = ticks_ms()
        while len(buf) < count:
            if ticks_diff(ticks_ms(), start) > timeout_ms:
                break
            available = getattr(self.uart, "any", lambda: 0)()
            if available > 0:
                needed = count - len(buf)
                chunk = self.uart.read(min(available, needed))
                if chunk:
                    buf.extend(chunk)
            await asyncio.sleep_ms(5)
        return bytes(buf)

    async def send_command(self, cmd, params=b"", timeout_ms=1000):
        """Send command packet and await acknowledgment response."""
        content = bytes([cmd]) + params
        packet = build_packet(PID_COMMAND, content, self.address)
        self.uart.write(packet)

        # Read packet header (2 bytes) + address (4 bytes) + PID (1 byte) + Length (2 bytes) = 9 bytes
        header = await self._read_exact(9, timeout_ms=timeout_ms)
        if len(header) < 9:
            return None, "Timeout waiting for AS608 response header"

        if header[0:2] != HEADER:
            return None, "Invalid AS608 response header: {}".format(header[0:2])

        pid = header[6]
        length = (header[7] << 8) | header[8]

        # Read payload (content) + checksum (2 bytes)
        body = await self._read_exact(length, timeout_ms=timeout_ms)
        if len(body) < length:
            return None, "Truncated AS608 response payload"

        content = body[:-2]
        recv_checksum = (body[-2] << 8) | body[-1]

        # Verify checksum
        calc_checksum = (pid + header[7] + header[8] + sum(content)) & 0xFFFF
        if calc_checksum != recv_checksum:
            return None, "Checksum mismatch (calc={}, recv={})".format(calc_checksum, recv_checksum)

        if len(content) < 1:
            return None, "Empty confirmation code in AS608 response"

        confirm_code = content[0]
        data = content[1:]
        return (confirm_code, data), None

    async def verify_password(self, password=None):
        """Verify sensor password (default 0x00000000)."""
        if password is None:
            password = self.password
        pwd_bytes = password.to_bytes(4, "big") if isinstance(password, int) else password
        res, err = await self.send_command(CMD_VFY_PWD, pwd_bytes)
        if err:
            return False, err
        code, _ = res
        return code == CONFIRM_OK, "Code: 0x{:02x}".format(code)

    async def capture_image(self):
        """Capture fingerprint image into ImageBuffer. Returns confirm code."""
        res, err = await self.send_command(CMD_GEN_IMG)
        if err:
            return None, err
        return res[0], None

    async def image_to_tz(self, buffer_id=1):
        """Generate character file from image in ImageBuffer to CharBuffer 1 or 2."""
        res, err = await self.send_command(CMD_IMG_2_TZ, bytes([buffer_id]))
        if err:
            return None, err
        return res[0], None

    async def fast_search(self, buffer_id=1, start_page=0, page_num=300):
        """Search library for template matching CharBuffer."""
        params = bytes([
            buffer_id,
            (start_page >> 8) & 0xFF, start_page & 0xFF,
            (page_num >> 8) & 0xFF, page_num & 0xFF
        ])
        res, err = await self.send_command(CMD_FAST_SEARCH, params)
        if err:
            return False, 0, 0, err
        code, data = res
        if code == CONFIRM_OK and len(data) >= 4:
            page_id = (data[0] << 8) | data[1]
            score = (data[2] << 8) | data[3]
            return True, page_id, score, "OK"
        elif code == CONFIRM_NOT_FOUND:
            return False, 0, 0, "Not found"
        else:
            return False, 0, 0, "Error code: 0x{:02x}".format(code)

    async def create_model(self):
        """Combine CharBuffer1 and CharBuffer2 into a unified model."""
        res, err = await self.send_command(CMD_REG_MODEL)
        if err:
            return False, err
        code, _ = res
        return code == CONFIRM_OK, "Code: 0x{:02x}".format(code)

    async def store_model(self, slot_id, buffer_id=1):
        """Store template from CharBuffer into Flash page slot_id."""
        params = bytes([
            buffer_id,
            (slot_id >> 8) & 0xFF, slot_id & 0xFF
        ])
        res, err = await self.send_command(CMD_STORE, params)
        if err:
            return False, err
        code, _ = res
        return code == CONFIRM_OK, "Code: 0x{:02x}".format(code)

    async def delete_slot(self, slot_id, count=1):
        """Delete `count` template slots starting from slot_id."""
        params = bytes([
            (slot_id >> 8) & 0xFF, slot_id & 0xFF,
            (count >> 8) & 0xFF, count & 0xFF
        ])
        res, err = await self.send_command(CMD_DEL_CHAR, params)
        if err:
            return False, err
        code, _ = res
        return code == CONFIRM_OK, "Code: 0x{:02x}".format(code)

    async def empty_database(self):
        """Delete all fingerprint templates in Flash."""
        res, err = await self.send_command(CMD_EMPTY)
        if err:
            return False, err
        code, _ = res
        return code == CONFIRM_OK, "Code: 0x{:02x}".format(code)

    async def set_led(self, mode=1, speed=50, color=1, count=0):
        """Control ring LED if supported by sensor model."""
        params = bytes([mode, speed, color, count])
        res, err = await self.send_command(CMD_AURA_LED, params)
        if err:
            return False, err
        code, _ = res
        return code == CONFIRM_OK, "Code: 0x{:02x}".format(code)

    # High-level async routines
    async def wait_finger(self, timeout_s=10):
        """Wait until a finger is placed on the sensor."""
        start = ticks_ms()
        timeout_ms = timeout_s * 1000
        while ticks_diff(ticks_ms(), start) < timeout_ms:
            code, err = await self.capture_image()
            if code == CONFIRM_OK:
                return True
            await asyncio.sleep_ms(150)
        return False

    async def wait_finger_lift(self, timeout_s=5):
        """Wait until user removes finger from sensor."""
        start = ticks_ms()
        timeout_ms = timeout_s * 1000
        while ticks_diff(ticks_ms(), start) < timeout_ms:
            code, _ = await self.capture_image()
            if code == CONFIRM_NO_FINGER:
                return True
            await asyncio.sleep_ms(100)
        return False

    async def search_once(self, buffer_id=1):
        """Capture image, convert to template, and search database in one shot."""
        code, err = await self.capture_image()
        if err or code != CONFIRM_OK:
            return False, 0, 0, err or "No finger"

        code, err = await self.image_to_tz(buffer_id=buffer_id)
        if err or code != CONFIRM_OK:
            return False, 0, 0, err or "Failed to extract character (code 0x{:02x})".format(code)

        return await self.fast_search(buffer_id=buffer_id)

    async def enroll_single_sample(self, slot_id, timeout_s=15):
        """
        Atomic enrollment step:
        1. Wait for finger touch.
        2. Capture image -> CharBuffer1.
        3. Copy to CharBuffer2 -> RegModel -> Store into slot_id.
        4. Wait for finger lift.
        Returns (success: bool, state: str, error: str)
        """
        # Step 1: Wait for finger
        got_finger = await self.wait_finger(timeout_s=timeout_s)
        if not got_finger:
            return False, "timeout", "Timed out waiting for finger"

        # Step 2: Extract template into CharBuffer1
        code, err = await self.image_to_tz(buffer_id=1)
        if err or code != CONFIRM_OK:
            return False, "poor_quality", "Image quality insufficient (code 0x{:02x})".format(code or 0)

        # Mirror into CharBuffer2 for RegModel
        code, err = await self.image_to_tz(buffer_id=2)
        if err or code != CONFIRM_OK:
            return False, "poor_quality", "Failed buffer generation"

        # Step 3: Combine model
        ok, err = await self.create_model()
        if not ok:
            return False, "combine_failed", err

        # Step 4: Store in target slot
        ok, err = await self.store_model(slot_id, buffer_id=1)
        if not ok:
            return False, "store_failed", err

        # Step 5: Wait for lift so user doesn't double-register the same press
        await self.wait_finger_lift(timeout_s=5)

        return True, "stored", None
