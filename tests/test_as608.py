"""Unit tests for AS608 optical fingerprint reader driver and protocol."""
import pytest
# pyrefly: ignore [missing-import]
from machine import UART
from drivers.as608 import AS608, build_packet, PID_ACK, CONFIRM_OK, CONFIRM_NOT_FOUND, CONFIRM_NO_FINGER


def make_ack_packet(confirm_code, return_data=b"", pid=PID_ACK):
    """Helper to generate a valid AS608 ACK packet."""
    content = bytes([confirm_code]) + return_data
    return build_packet(pid, content)


@pytest.fixture
def mock_uart():
    return UART(port=2, baudrate=57600)


@pytest.fixture
def sensor(mock_uart):
    return AS608(mock_uart)


def test_packet_builder():
    """Verify packet framing and 16-bit checksum calculation."""
    # Command 0x13 (VfyPwd) with 4 zero bytes
    packet = build_packet(0x01, b"\x13\x00\x00\x00\x00")
    # Header (2) + Addr (4) + PID (1) + Len (2) + Content (5) + Checksum (2) = 16 bytes
    assert len(packet) == 16
    assert packet[:2] == b"\xef\x01"
    assert packet[2:6] == b"\xff\xff\xff\xff"
    assert packet[6] == 0x01
    # Length = len(content) + 2 = 7 -> \x00\x07
    assert packet[7:9] == b"\x00\x07"
    # Checksum = PID(1) + LenH(0) + LenL(7) + Content(0x13) = 1 + 7 + 19 = 27 = 0x001B
    assert packet[14:16] == b"\x00\x1b"


@pytest.mark.asyncio
async def test_verify_password_success(sensor, mock_uart):
    """Test successful handshake password verification."""
    mock_uart.feed_rx(make_ack_packet(CONFIRM_OK))
    ok, err = await sensor.verify_password()
    assert ok is True
    assert "0x00" in err


@pytest.mark.asyncio
async def test_fast_search_found(sensor, mock_uart):
    """Test fast_search finding matching fingerprint slot 12 with score 95."""
    # Return data: 2 bytes page_id (0x00, 0x0C) + 2 bytes score (0x00, 0x5F)
    payload_data = (12).to_bytes(2, "big") + (95).to_bytes(2, "big")
    mock_uart.feed_rx(make_ack_packet(CONFIRM_OK, payload_data))

    found, page_id, score, err = await sensor.fast_search(buffer_id=1)
    assert found is True
    assert page_id == 12
    assert score == 95


@pytest.mark.asyncio
async def test_fast_search_not_found(sensor, mock_uart):
    """Test fast_search when no match exists."""
    mock_uart.feed_rx(make_ack_packet(CONFIRM_NOT_FOUND))
    found, page_id, score, err = await sensor.fast_search(buffer_id=1)
    assert found is False
    assert "Not found" in err


@pytest.mark.asyncio
async def test_delete_slot(sensor, mock_uart):
    """Test deleting fingerprint template slot."""
    mock_uart.feed_rx(make_ack_packet(CONFIRM_OK))
    ok, err = await sensor.delete_slot(15)
    assert ok is True


@pytest.mark.asyncio
async def test_enroll_single_sample_flow(sensor, mock_uart):
    """Test full atomic enroll sequence (detect -> gen char -> combine -> store -> lift)."""
    # 1. wait_finger (capture_image -> OK)
    mock_uart.feed_rx(make_ack_packet(CONFIRM_OK))
    # 2. image_to_tz(1) -> OK
    mock_uart.feed_rx(make_ack_packet(CONFIRM_OK))
    # 3. image_to_tz(2) -> OK
    mock_uart.feed_rx(make_ack_packet(CONFIRM_OK))
    # 4. create_model (RegModel) -> OK
    mock_uart.feed_rx(make_ack_packet(CONFIRM_OK))
    # 5. store_model -> OK
    mock_uart.feed_rx(make_ack_packet(CONFIRM_OK))
    # 6. wait_finger_lift (capture_image -> NO_FINGER)
    mock_uart.feed_rx(make_ack_packet(CONFIRM_NO_FINGER))

    ok, state, err = await sensor.enroll_single_sample(slot_id=20, timeout_s=2)
    assert ok is True
    assert state == "stored"
    assert err is None
