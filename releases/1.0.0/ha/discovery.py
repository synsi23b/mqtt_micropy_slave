"""Home Assistant MQTT Auto-Discovery payload generator with dynamic component entities."""
import json


class HADiscovery:
    """Generates Home Assistant MQTT discovery payloads for dynamic entities."""

    def __init__(self, config, component_manager=None):
        self.config = config
        self.component_manager = component_manager
        self.device_id = config.device_id
        self.device_name = config.device_name
        self.base_topic = config.base_topic
        self.ha_prefix = config.ha_prefix

    @property
    def device_info(self):
        # Load local version if available
        sw_ver = "1.0.0"
        try:
            with open("version.json", "r") as f:
                sw_ver = json.load(f).get("version", "1.0.0")
        except Exception:
            pass

        return {
            "identifiers": [self.device_id],
            "name": self.device_name,
            "model": "ESP32 Universal Edge Slave",
            "manufacturer": "MicroPython DIY",
            "sw_version": sw_ver
        }

    def generate_discovery_messages(self):
        """Generate list of (discovery_topic, payload_dict) tuples for all entities."""
        messages = []
        avail_topic = "{}/availability".format(self.base_topic)
        status_topic = "{}/status".format(self.base_topic)
        abort_topic = "{}/job/abort".format(self.base_topic)
        run_topic = "{}/job/run".format(self.base_topic)
        events_topic = "{}/events".format(self.base_topic)
        ota_topic = "{}/ota/update".format(self.base_topic)

        # 1. Status Sensor
        status_obj = "status"
        status_topic_cfg = "{}/sensor/{}/{}/config".format(self.ha_prefix, self.device_id, status_obj)
        messages.append((status_topic_cfg, {
            "name": "{} Status".format(self.device_name),
            "unique_id": "{}_{}".format(self.device_id, status_obj),
            "state_topic": status_topic,
            "value_template": "{{ value_json.state }}",
            "json_attributes_topic": status_topic,
            "availability_topic": avail_topic,
            "icon": "mdi:chip",
            "device": self.device_info
        }))

        # 2. Emergency Abort Button
        abort_obj = "abort_button"
        abort_topic_cfg = "{}/button/{}/{}/config".format(self.ha_prefix, self.device_id, abort_obj)
        messages.append((abort_topic_cfg, {
            "name": "{} Abort Job".format(self.device_name),
            "unique_id": "{}_{}".format(self.device_id, abort_obj),
            "command_topic": abort_topic,
            "payload_press": json.dumps({"reason": "ha_abort_button"}),
            "availability_topic": avail_topic,
            "icon": "mdi:alert-octagon",
            "device": self.device_info
        }))

        # 3. Last Event Sensor
        event_obj = "last_event"
        event_topic_cfg = "{}/sensor/{}/{}/config".format(self.ha_prefix, self.device_id, event_obj)
        messages.append((event_topic_cfg, {
            "name": "{} Last Event".format(self.device_name),
            "unique_id": "{}_{}".format(self.device_id, event_obj),
            "state_topic": events_topic,
            "value_template": "{{ value_json.event }}",
            "json_attributes_topic": events_topic,
            "availability_topic": avail_topic,
            "icon": "mdi:bell-ring",
            "device": self.device_info
        }))

        # 4. OTA Update Trigger Button
        if self.config.ota_enabled or self.config.ota_manifest_url:
            ota_obj = "ota_update"
            ota_topic_cfg = "{}/button/{}/{}/config".format(self.ha_prefix, self.device_id, ota_obj)
            messages.append((ota_topic_cfg, {
                "name": "{} Check & Apply OTA".format(self.device_name),
                "unique_id": "{}_{}".format(self.device_id, ota_obj),
                "command_topic": ota_topic,
                "payload_press": json.dumps({}),
                "availability_topic": avail_topic,
                "icon": "mdi:cloud-download",
                "device": self.device_info
            }))

        # 5. Routine Trigger Buttons
        routines = self.config.routines
        for routine_name in routines.keys():
            obj_id = "routine_{}".format(routine_name)
            disc_topic = "{}/button/{}/{}/config".format(self.ha_prefix, self.device_id, obj_id)
            nice_name = routine_name.replace("_", " ").title()
            icon = "mdi:play-circle-outline"
            if "unlock" in routine_name:
                icon = "mdi:door-open"
            elif "lock" in routine_name:
                icon = "mdi:door-closed"
            elif "press" in routine_name or "switchbot" in routine_name:
                icon = "mdi:gesture-tap-button"

            messages.append((disc_topic, {
                "name": "{} Run {}".format(self.device_name, nice_name),
                "unique_id": "{}_{}".format(self.device_id, obj_id),
                "command_topic": run_topic,
                "payload_press": json.dumps({"job": routine_name}),
                "availability_topic": avail_topic,
                "icon": icon,
                "device": self.device_info
            }))

        # 6. Dynamic Input Sensors (Digital Inputs / Status LEDs & Analogs)
        if self.component_manager:
            # Digital Inputs (binary_sensor)
            for comp_id, comp in self.component_manager.get_reporting_inputs():
                obj_id = "input_{}".format(comp_id)
                disc_topic = "{}/binary_sensor/{}/{}/config".format(self.ha_prefix, self.device_id, obj_id)
                payload = {
                    "name": "{} {}".format(self.device_name, comp_id.replace("_", " ").title()),
                    "unique_id": "{}_{}".format(self.device_id, obj_id),
                    "state_topic": "{}/input/{}".format(self.base_topic, comp_id),
                    "payload_on": "1",
                    "payload_off": "0",
                    "availability_topic": avail_topic,
                    "device": self.device_info
                }
                if comp.ha_device_class:
                    payload["device_class"] = comp.ha_device_class
                messages.append((disc_topic, payload))

            # Analog Inputs (sensor)
            for comp_id, comp in self.component_manager.get_reporting_analogs():
                obj_id = "analog_{}".format(comp_id)
                disc_topic = "{}/sensor/{}/{}/config".format(self.ha_prefix, self.device_id, obj_id)
                messages.append((disc_topic, {
                    "name": "{} {}".format(self.device_name, comp_id.replace("_", " ").title()),
                    "unique_id": "{}_{}".format(self.device_id, obj_id),
                    "state_topic": "{}/input/{}".format(self.base_topic, comp_id),
                    "value_template": "{{ value_json.voltage }}",
                    "unit_of_measurement": "V",
                    "device_class": comp.ha_device_class or "voltage",
                    "availability_topic": avail_topic,
                    "device": self.device_info
                }))

        return messages

    async def publish_discovery(self, mqtt_client):
        """Publish all discovery messages via the MQTT client."""
        for topic, payload in self.generate_discovery_messages():
            await mqtt_client.publish(topic, payload, retain=True)
