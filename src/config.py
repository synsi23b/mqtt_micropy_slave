"""Configuration manager for MicroPython MQTT Edge Slave."""
import json

DEFAULT_CONFIG = {
    "device": {
        "id": "esp32_slave",
        "name": "Edge Slave",
        "heartbeat_interval_s": 30
    },
    "wifi": {
        "ssid": "",
        "password": "",
        "connect_timeout_s": 20,
        "max_retries": 5
    },
    "mqtt": {
        "host": "127.0.0.1",
        "port": 1883,
        "user": "",
        "password": "",
        "keepalive": 60,
        "base_topic": "slave/front_door",
        "ha_discovery_prefix": "homeassistant"
    },
    "hardware": {
        "solenoid": {
            "pin": 23,
            "active_high": True,
            "default_pulse_ms": 3000,
            "max_pulse_ms": 10000
        },
        "buzzer": {
            "pin": 19,
            "active_high": True
        },
        "led_status": {
            "pin": 2,
            "active_high": True
        },
        "door_sensor": {
            "pin": 4,
            "pull_up": True,
            "active_low": True
        },
        "as608": {
            "uart_id": 2,
            "tx_pin": 17,
            "rx_pin": 16,
            "baudrate": 57600,
            "password": 0,
            "address": 0xFFFFFFFF,
            "wak_pin": 18,
            "use_wak": False
        }
    },
    "routines": {
        "door_unlock": [
            {"action": "pulse", "pin": "buzzer", "duration_ms": 100},
            {"action": "pulse", "pin": "solenoid", "duration_ms": 3000}
        ],
        "door_lock": [
            {"action": "digital_write", "pin": "solenoid", "state": 0}
        ]
    }
}


def deep_merge(target, source):
    """Recursively merge source dictionary into target."""
    for key, value in source.items():
        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target


class Config:
    def __init__(self, config_dict=None):
        import copy
        # Deep copy default config
        self._data = json.loads(json.dumps(DEFAULT_CONFIG))
        if config_dict:
            deep_merge(self._data, config_dict)

    @classmethod
    def load_from_file(cls, path="config.json"):
        """Load configuration from JSON file, falling back to defaults if missing."""
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            with open(path, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    deep_merge(cfg, loaded)
        except OSError:
            pass  # File missing, use defaults
        except ValueError:
            pass  # Malformed JSON
        return cls(cfg)

    def get(self, *keys, default=None):
        """Retrieve nested configuration value via key path."""
        curr = self._data
        for k in keys:
            if not isinstance(curr, dict) or k not in curr:
                return default
            curr = curr[k]
        return curr

    @property
    def device_id(self):
        return self.get("device", "id", default="esp32_slave")

    @property
    def device_name(self):
        return self.get("device", "name", default="Edge Slave")

    @property
    def heartbeat_interval(self):
        return self.get("device", "heartbeat_interval_s", default=30)

    @property
    def wifi_ssid(self):
        return self.get("wifi", "ssid", default="")

    @property
    def wifi_password(self):
        return self.get("wifi", "password", default="")

    @property
    def mqtt_host(self):
        return self.get("mqtt", "host", default="127.0.0.1")

    @property
    def mqtt_port(self):
        return self.get("mqtt", "port", default=1883)

    @property
    def mqtt_user(self):
        return self.get("mqtt", "user", default="")

    @property
    def mqtt_password(self):
        return self.get("mqtt", "password", default="")

    @property
    def base_topic(self):
        base = self.get("mqtt", "base_topic")
        if not base:
            base = "slave/" + self.device_id
        return base.rstrip("/")

    @property
    def ha_prefix(self):
        return self.get("mqtt", "ha_discovery_prefix", default="homeassistant").rstrip("/")

    @property
    def routines(self):
        return self.get("routines", default={})

    @property
    def raw_data(self):
        return self._data
