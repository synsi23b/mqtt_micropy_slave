"""Unit tests for Home Assistant MQTT Auto-Discovery generator."""
import json
from config import Config
from ha.discovery import HADiscovery


def test_ha_discovery_payloads():
    cfg = Config({
        "device": {"id": "esp32_test", "name": "Front Door Tester"},
        "mqtt": {"base_topic": "slave/test_door", "ha_discovery_prefix": "homeassistant"},
        "routines": {
            "door_unlock": [{"action": "pulse", "pin": "solenoid", "duration_ms": 3000}],
            "door_lock": [{"action": "digital_write", "pin": "solenoid", "state": 0}]
        }
    })
    ha = HADiscovery(cfg)
    messages = ha.generate_discovery_messages()

    topics = [topic for topic, _ in messages]
    payloads = {topic: payload for topic, payload in messages}

    # 1. Status Sensor Discovery
    status_topic = "homeassistant/sensor/esp32_test/status/config"
    assert status_topic in topics
    status_cfg = payloads[status_topic]
    assert status_cfg["state_topic"] == "slave/test_door/status"
    assert status_cfg["availability_topic"] == "slave/test_door/availability"
    assert status_cfg["unique_id"] == "esp32_test_status"
    assert status_cfg["device"]["identifiers"] == ["esp32_test"]

    # 2. Abort Button Discovery
    abort_topic = "homeassistant/button/esp32_test/abort_button/config"
    assert abort_topic in topics
    abort_cfg = payloads[abort_topic]
    assert abort_cfg["command_topic"] == "slave/test_door/job/abort"

    # 3. Routine Buttons
    unlock_topic = "homeassistant/button/esp32_test/routine_door_unlock/config"
    assert unlock_topic in topics
    unlock_cfg = payloads[unlock_topic]
    assert unlock_cfg["command_topic"] == "slave/test_door/job/run"
    assert json.loads(unlock_cfg["payload_press"])["job"] == "door_unlock"
