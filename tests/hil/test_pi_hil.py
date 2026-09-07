"""
Raspberry Pi Hardware-in-the-Loop (HIL) Test Suite.
Run this script on a Raspberry Pi wired to the ESP32 to verify physical GPIO timings,
PWM duty cycle/frequency, bidirectional digital I/O, and AS608 UART packet exchanges.

Wiring schematic: docs/HARDWARE_WIRING.md#7-raspberry-pi-hardware-in-the-loop-hil-test-wiring
"""
import pytest
import time
import json
import os

# Target ESP32 DUT Configuration
BROKER_HOST = os.environ.get("MQTT_BROKER", "127.0.0.1")
DEVICE_ID = os.environ.get("DEVICE_ID", "esp32_hil_dut")

# Raspberry Pi Physical Pin Mapping (BCM Numbers)
SOLENOID_SENSE_PIN = int(os.environ.get("PI_SOLENOID_PIN", "17"))          # Physical Pin 11
DIGITAL_OUT_SENSE_PIN = int(os.environ.get("PI_DIGITAL_OUT_PIN", "27"))     # Physical Pin 13
DIGITAL_IN_STIMULUS_PIN = int(os.environ.get("PI_INPUT_STIMULUS_PIN", "22"))# Physical Pin 15
PWM_SENSE_PIN = int(os.environ.get("PI_PWM_PIN", "23"))                    # Physical Pin 16
UART_PORT = os.environ.get("PI_UART_PORT", "/dev/serial0")
UART_BAUDRATE = int(os.environ.get("PI_UART_BAUDRATE", "57600"))


def is_pi_hardware():
    """Detect if running on a real Raspberry Pi with GPIO access."""
    try:
        # pyrefly: ignore [missing-import]
        import gpiod
        return True
    except ImportError:
        try:
            # pyrefly: ignore [missing-import]
            import RPi.GPIO
            return True
        except ImportError:
            return False


def is_uart_available():
    """Detect if the configured serial port exists on the Pi."""
    return os.path.exists(UART_PORT)


def is_remote_hil_available():
    """Detect if remote Pi is configured in .env and online on port 22."""
    if is_pi_hardware():
        return False
    try:
        import sys
        sys_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from tools.hil_manager import load_hil_config, is_port_open
        cfg = load_hil_config()
        host = cfg.get("host")
        port = cfg.get("port", 22)
        return bool(host and is_port_open(host, port, timeout=0.8))
    except Exception:
        return False


require_pi_hardware = pytest.mark.skipif(
    not is_pi_hardware(),
    reason="Direct HIL execution requires Raspberry Pi GPIO environment (gpiod or RPi.GPIO)"
)


def get_gpio_chip():
    # pyrefly: ignore [missing-import]
    import gpiod
    if os.path.exists("/dev/gpiochip4"):
        return gpiod.Chip("gpiochip4")  # Raspberry Pi 5 RP1
    return gpiod.Chip("gpiochip0")      # Raspberry Pi 4 / older


def measure_pwm_timing(line, timeout_s=3.0, samples=5):
    """
    Measure PWM frequency and high-time pulse width using high-resolution timestamps.
    Returns: (frequency_hz, high_time_us)
    """
    high_times_us = []
    periods_us = []
    deadline = time.time() + timeout_s

    # Wait for initial low
    while line.get_value() == 1 and time.time() < deadline:
        pass

    for _ in range(samples):
        # 1. Wait for rising edge
        while line.get_value() == 0 and time.time() < deadline:
            pass
        t_rise = time.perf_counter_ns()

        # 2. Wait for falling edge
        while line.get_value() == 1 and time.time() < deadline:
            pass
        t_fall = time.perf_counter_ns()

        # 3. Wait for next rising edge to determine full period
        while line.get_value() == 0 and time.time() < deadline:
            pass
        t_rise_next = time.perf_counter_ns()

        high_us = (t_fall - t_rise) / 1000.0
        period_us = (t_rise_next - t_rise) / 1000.0

        if high_us > 0 and period_us > high_us:
            high_times_us.append(high_us)
            periods_us.append(period_us)

    if not high_times_us or not periods_us:
        raise TimeoutError("Failed to capture stable PWM signal on GPIO pin")

    avg_high_us = sum(high_times_us) / len(high_times_us)
    avg_period_us = sum(periods_us) / len(periods_us)
    freq_hz = 1_000_000.0 / avg_period_us

    return freq_hz, avg_high_us


# ==============================================================================
# 1. SOLENOID TIMED PULSE VERIFICATION
# ==============================================================================
@require_pi_hardware
def test_measure_solenoid_physical_pulse_width():
    """
    Triggers 'door_unlock' over MQTT, measures the physical HIGH duration
    on the Pi's input GPIO pin connected to ESP32's solenoid pin,
    and asserts timing accuracy and low failsafe.
    """
    import paho.mqtt.client as paho
    # pyrefly: ignore [missing-import]
    import gpiod

    chip = get_gpio_chip()
    line = chip.get_line(SOLENOID_SENSE_PIN)
    line.request(consumer="hil_solenoid", type=gpiod.LINE_REQ_DIR_IN)

    client = paho.Client(client_id="pi_hil_solenoid_runner")
    client.connect(BROKER_HOST, 1883, 60)
    client.loop_start()

    try:
        # Trigger 1000ms unlock pulse
        command_topic = f"slave/{DEVICE_ID}/job/run"
        client.publish(command_topic, json.dumps({"job": "door_unlock", "params": {"duration_ms": 1000}}))

        # Wait for rising edge
        start_time = None
        timeout = time.time() + 3.0
        while time.time() < timeout:
            if line.get_value() == 1:
                start_time = time.perf_counter()
                break
            time.sleep(0.001)

        assert start_time is not None, f"ESP32 Solenoid pin never went HIGH on Pi pin {SOLENOID_SENSE_PIN}"

        # Wait for falling edge
        end_time = None
        timeout = time.time() + 2.0
        while time.time() < timeout:
            if line.get_value() == 0:
                end_time = time.perf_counter()
                break
            time.sleep(0.001)

        assert end_time is not None, "ESP32 Solenoid pin never returned to LOW (coil safety risk!)"

        pulse_duration_ms = (end_time - start_time) * 1000.0
        print(f"\n[HIL Result] Measured Solenoid Pulse: {pulse_duration_ms:.2f} ms")

        # Assert 1000ms ± 150ms physical tolerance
        assert 850.0 <= pulse_duration_ms <= 1150.0

    finally:
        line.release()
        chip.close()
        client.loop_stop()
        client.disconnect()


# ==============================================================================
# 2. PWM SERVO FREQUENCY & DUTY CYCLE MEASUREMENT
# ==============================================================================
@require_pi_hardware
def test_measure_servo_pwm_duty_and_frequency():
    """
    Commands the ESP32 to set a servo angle over MQTT, and measures the physical
    PWM carrier frequency (~50Hz) and high pulse width (500us - 2500us) on the Pi.
    """
    import paho.mqtt.client as paho
    # pyrefly: ignore [missing-import]
    import gpiod

    chip = get_gpio_chip()
    line = chip.get_line(PWM_SENSE_PIN)
    line.request(consumer="hil_pwm", type=gpiod.LINE_REQ_DIR_IN)

    client = paho.Client(client_id="pi_hil_pwm_runner")
    client.connect(BROKER_HOST, 1883, 60)
    client.loop_start()

    try:
        command_topic = f"slave/{DEVICE_ID}/job/run"

        # 1. Set servo to 90 degrees (~1500us pulse)
        job_90 = {"steps": [{"action": "servo_set", "target": "servo", "angle": 90}]}
        client.publish(command_topic, json.dumps(job_90))
        time.sleep(0.3)

        freq_hz, pulse_90_us = measure_pwm_timing(line, timeout_s=3.0)
        print(f"\n[HIL Result] Servo 90°: Frequency={freq_hz:.2f}Hz, HighTime={pulse_90_us:.1f}us")

        # 50Hz standard RC servo frequency (allow 45Hz - 55Hz physical tolerance)
        assert 45.0 <= freq_hz <= 55.0
        # 90 degrees center pulse: nominal 1500us (allow 1350us - 1650us)
        assert 1350.0 <= pulse_90_us <= 1650.0

        # 2. Set servo to 0 degrees (~500us - 600us pulse)
        job_0 = {"steps": [{"action": "servo_set", "target": "servo", "angle": 0}]}
        client.publish(command_topic, json.dumps(job_0))
        time.sleep(0.3)

        _, pulse_0_us = measure_pwm_timing(line, timeout_s=3.0)
        print(f"[HIL Result] Servo 0°: HighTime={pulse_0_us:.1f}us")

        # 0 degrees min pulse: nominal 500us (allow 450us - 750us)
        assert 450.0 <= pulse_0_us <= 750.0
        assert pulse_90_us > pulse_0_us + 600.0, "90° pulse must be significantly wider than 0° pulse"

    finally:
        line.release()
        chip.close()
        client.loop_stop()
        client.disconnect()


# ==============================================================================
# 3. DIGITAL OUTPUT STATE SWITCHING (LED / BUZZER / RELAY)
# ==============================================================================
@require_pi_hardware
def test_digital_output_state_switching():
    """
    Commands digital writes (HIGH / LOW) to an output component and verifies
    the static electrical logic level on the Pi's sensing input pin.
    """
    import paho.mqtt.client as paho
    # pyrefly: ignore [missing-import]
    import gpiod

    chip = get_gpio_chip()
    line = chip.get_line(DIGITAL_OUT_SENSE_PIN)
    line.request(consumer="hil_digital_out", type=gpiod.LINE_REQ_DIR_IN)

    client = paho.Client(client_id="pi_hil_dio_runner")
    client.connect(BROKER_HOST, 1883, 60)
    client.loop_start()

    try:
        command_topic = f"slave/{DEVICE_ID}/job/run"

        # Drive HIGH
        client.publish(command_topic, json.dumps({"steps": [{"action": "digital_write", "target": "buzzer", "state": 1}]}))
        time.sleep(0.2)
        assert line.get_value() == 1, f"Expected HIGH (1) on Pi pin {DIGITAL_OUT_SENSE_PIN}"

        # Drive LOW
        client.publish(command_topic, json.dumps({"steps": [{"action": "digital_write", "target": "buzzer", "state": 0}]}))
        time.sleep(0.2)
        assert line.get_value() == 0, f"Expected LOW (0) on Pi pin {DIGITAL_OUT_SENSE_PIN}"

        print(f"\n[HIL Result] Verified static Digital Output HIGH/LOW transitions on pin {DIGITAL_OUT_SENSE_PIN}")

    finally:
        line.release()
        chip.close()
        client.loop_stop()
        client.disconnect()


# ==============================================================================
# 4. DIGITAL INPUT STIMULUS & LIVE MQTT REPORTING (DOOR SENSOR / APPLIANCE LED)
# ==============================================================================
@require_pi_hardware
def test_digital_input_stimulus_and_mqtt_reporting():
    """
    The Pi drives an electrical signal into an ESP32 digital input pin,
    and asserts that the ESP32 detects the transition and publishes the new
    state to MQTT ('slave/<id>/input/<component>').
    """
    import paho.mqtt.client as paho
    # pyrefly: ignore [missing-import]
    import gpiod

    chip = get_gpio_chip()
    line = chip.get_line(DIGITAL_IN_STIMULUS_PIN)
    line.request(consumer="hil_stimulus", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0])

    received_states = []

    def on_message(c, userdata, msg):
        received_states.append((msg.topic, msg.payload.decode("utf-8")))

    client = paho.Client(client_id="pi_hil_input_runner")
    client.on_message = on_message
    client.connect(BROKER_HOST, 1883, 60)
    client.subscribe(f"slave/{DEVICE_ID}/input/#")
    client.loop_start()

    try:
        # 1. Drive HIGH from Pi
        line.set_value(1)
        time.sleep(0.4)

        # 2. Drive LOW from Pi
        line.set_value(0)
        time.sleep(0.4)

        # Verify ESP32 published input state updates
        payloads = [payload for _, payload in received_states]
        print(f"\n[HIL Result] Input stimulus MQTT states received: {payloads}")
        assert len(payloads) >= 2, "ESP32 did not publish state change events for stimulus transitions"
        assert "1" in payloads and "0" in payloads, "Expected both '1' and '0' state transitions in MQTT stream"

    finally:
        line.release()
        chip.close()
        client.loop_stop()
        client.disconnect()


# ==============================================================================
# 5. AS608 UART PERIPHERAL PACKET EMULATION
# ==============================================================================
@require_pi_hardware
def test_uart_as608_peripheral_emulation():
    """
    Emulates an AS608 optical fingerprint reader on the Pi's hardware serial port.
    When the ESP32 initiates fingerprint search, the Pi reads the AS608 command packet,
    transmits a valid ACK response, and verifies the resulting MQTT event.
    """
    if not is_uart_available():
        pytest.skip(f"UART port {UART_PORT} not found on Raspberry Pi. Connect serial cable or enable /dev/serial0.")

    import serial
    import paho.mqtt.client as paho

    # AS608 protocol helpers
    HEADER = b"\xef\x01"
    DEFAULT_ADDR = b"\xff\xff\xff\xff"
    PID_ACK = 0x07
    CONFIRM_OK = 0x00

    def make_ack(confirm_code, return_data=b""):
        content = bytes([confirm_code]) + return_data
        length = len(content) + 2
        length_bytes = bytes([(length >> 8) & 0xFF, length & 0xFF])
        packet_pre = bytes([PID_ACK]) + length_bytes + content
        checksum = (PID_ACK + (length >> 8) + (length & 0xFF) + sum(content)) & 0xFFFF
        checksum_bytes = bytes([(checksum >> 8) & 0xFF, checksum & 0xFF])
        return HEADER + DEFAULT_ADDR + packet_pre + checksum_bytes

    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1.0)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    event_messages = []

    def on_event(c, userdata, msg):
        try:
            event_messages.append(json.loads(msg.payload.decode("utf-8")))
        except Exception:
            pass

    client = paho.Client(client_id="pi_hil_uart_runner")
    client.on_message = on_event
    client.connect(BROKER_HOST, 1883, 60)
    client.subscribe(f"slave/{DEVICE_ID}/events")
    client.loop_start()

    try:
        # Trigger AS608 search event on ESP32
        command_topic = f"slave/{DEVICE_ID}/job/run"
        search_job = {"steps": [{"action": "as608_search_event"}]}
        client.publish(command_topic, json.dumps(search_job))

        # Pi UART listener: Expect command packet from ESP32
        # Command packets start with \xef\x01\xff\xff\xff\xff\x01...
        deadline = time.time() + 4.0
        rx_bytes = bytearray()
        while time.time() < deadline:
            if ser.in_waiting > 0:
                rx_bytes.extend(ser.read(ser.in_waiting))
                if HEADER in rx_bytes and len(rx_bytes) >= 12:
                    break
            time.sleep(0.01)

        assert HEADER in rx_bytes, f"Pi never received AS608 header (0xEF01) from ESP32 on {UART_PORT}"
        print(f"\n[HIL Result] Intercepted AS608 UART command from ESP32: {rx_bytes.hex()}")

        # Emulate successful match: Slot #5, Confidence Score 92
        match_data = (5).to_bytes(2, "big") + (92).to_bytes(2, "big")
        ack_packet = make_ack(CONFIRM_OK, match_data)
        ser.write(ack_packet)
        ser.flush()

        # Wait for ESP32 to publish 'fingerprint_scanned' event to MQTT
        timeout = time.time() + 3.0
        matched_event = None
        while time.time() < timeout:
            for ev in event_messages:
                if ev.get("event") == "fingerprint_scanned" and ev.get("finger_id") == 5:
                    matched_event = ev
                    break
            if matched_event:
                break
            time.sleep(0.05)

        assert matched_event is not None, "ESP32 did not publish expected fingerprint_scanned MQTT event for Slot 5"
        print(f"[HIL Result] Verified full AS608 UART emulation cycle -> MQTT event: {matched_event}")

    finally:
        ser.close()
        client.loop_stop()
        client.disconnect()


# ==============================================================================
# 6. AUTOMATED REMOTE HIL RUNNER (RUNS WHEN PI IS DETECTED ONLINE)
# ==============================================================================
@pytest.mark.skipif(
    not is_remote_hil_available(),
    reason="Remote HIL test requires Raspberry Pi configured in .env / .hil_config.json and online on network."
)
def test_remote_hil_execution_on_pi():
    """
    When running on a workstation and the Raspberry Pi is online on WLAN,
    automatically syncs code to the Pi and runs the HIL test suite over SSH.
    """
    import sys
    import argparse
    sys_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from tools.hil_manager import load_hil_config, cmd_run

    cfg = load_hil_config()
    args = argparse.Namespace(
        host=cfg["host"],
        user=cfg.get("user", "pi"),
        port=cfg.get("port", 22),
        key=cfg.get("key_file"),
        device_id=cfg.get("device_id", "esp32_hil_dut"),
        no_sync=False,
        filter=None
    )
    ret = cmd_run(args)
    assert ret == 0, f"Remote HIL tests on {cfg['host']} failed with exit code {ret}"

