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
    "ota": {
        "enabled": False,
        "manifest_url": "",
        "check_on_boot": True,
        "check_interval_s": 86400
    },
    "components": {
        "solenoid": {
            "type": "solenoid",
            "pin": 23,
            "active_high": True,
            "default_pulse_ms": 3000,
            "max_pulse_ms": 10000
        },
        "buzzer": {
            "type": "digital_out",
            "pin": 19,
            "active_high": True
        },
        "led_status": {
            "type": "digital_out",
            "pin": 2,
            "active_high": True
        },
        "door_sensor": {
            "type": "digital_in",
            "pin": 4,
            "pull": "up",
            "invert": True,
            "report_changes": True,
            "ha_device_class": "door"
        },
        "as608": {
            "type": "as608",
            "uart_id": 2,
            "tx_pin": 17,
            "rx_pin": 16,
            "baudrate": 57600,
            "password": 0,
            "address": 0xFFFFFFFF
        }
    },
    "routines": {
        "door_unlock": [
            {"action": "pulse", "target": "buzzer", "duration_ms": 100},
            {"action": "pulse", "target": "solenoid", "duration_ms": 3000}
        ],
        "door_lock": [
            {"action": "digital_write", "target": "solenoid", "state": 0}
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


def migrate_hardware_to_components(data):
    """Backwards-compatibility: convert legacy 'hardware' block to 'components' if missing."""
    if "hardware" in data and "components" not in data:
        comps = {}
        hw = data["hardware"]
        if "solenoid" in hw:
            sol = hw["solenoid"]
            comps["solenoid"] = {
                "type": "solenoid",
                "pin": sol.get("pin", 23),
                "active_high": sol.get("active_high", True),
                "default_pulse_ms": sol.get("default_pulse_ms", 3000),
                "max_pulse_ms": sol.get("max_pulse_ms", 10000)
            }
        if "buzzer" in hw:
            comps["buzzer"] = {
                "type": "digital_out",
                "pin": hw["buzzer"].get("pin", 19),
                "active_high": hw["buzzer"].get("active_high", True)
            }
        if "led_status" in hw:
            comps["led_status"] = {
                "type": "digital_out",
                "pin": hw["led_status"].get("pin", 2),
                "active_high": hw["led_status"].get("active_high", True)
            }
        if "door_sensor" in hw:
            ds = hw["door_sensor"]
            comps["door_sensor"] = {
                "type": "digital_in",
                "pin": ds.get("pin", 4),
                "pull": "up" if ds.get("pull_up") else "none",
                "invert": ds.get("active_low", False),
                "report_changes": True
            }
        if "as608" in hw:
            sensor = hw["as608"]
            comps["as608"] = {
                "type": "as608",
                "uart_id": sensor.get("uart_id", 2),
                "tx_pin": sensor.get("tx_pin", 17),
                "rx_pin": sensor.get("rx_pin", 16),
                "baudrate": sensor.get("baudrate", 57600),
                "password": sensor.get("password", 0),
                "address": sensor.get("address", 0xFFFFFFFF)
            }
        data["components"] = comps


class Config:
    def __init__(self, config_dict=None):
        self._data = json.loads(json.dumps(DEFAULT_CONFIG))
        if config_dict:
            deep_merge(self._data, config_dict)
        migrate_hardware_to_components(self._data)

    @classmethod
    def load_from_file(cls, path="config.json"):
        """Load configuration from JSON file, falling back to defaults if missing."""
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            with open(path, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    deep_merge(cfg, loaded)
                    migrate_hardware_to_components(cfg)
        except OSError:
            pass
        except ValueError:
            pass
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
    def components(self):
        return self.get("components", default={})

    @property
    def ota_enabled(self):
        return self.get("ota", "enabled", default=False)

    @property
    def ota_manifest_url(self):
        return self.get("ota", "manifest_url", default="")

    @property
    def ota_check_on_boot(self):
        return self.get("ota", "check_on_boot", default=True)

    @property
    def ota_check_interval(self):
        return self.get("ota", "check_interval_s", default=86400)

    @property
    def routines(self):
        return self.get("routines", default={})

    @property
    def raw_data(self):
        return self._data
