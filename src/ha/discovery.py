"""Home Assistant MQTT Auto-Discovery payload generator."""
import json


class HADiscovery:
    """Generates Home Assistant MQTT discovery payloads for entities."""

    def __init__(self, config):
        self.config = config
        self.device_id = config.device_id
        self.device_name = config.device_name
        self.base_topic = config.base_topic
        self.ha_prefix = config.ha_prefix

    @property
    def device_info(self):
        return {
            "identifiers": [self.device_id],
            "name": self.device_name,
            "model": "ESP32 MQTT Edge Slave",
            "manufacturer": "MicroPython DIY",
            "sw_version": "1.0.0"
        }

    def generate_discovery_messages(self):
        """
        Generate list of (discovery_topic, payload_dict) tuples for all entities.
        """
        messages = []
        avail_topic = "{}/availability".format(self.base_topic)
        status_topic = "{}/status".format(self.base_topic)
        abort_topic = "{}/job/abort".format(self.base_topic)
        run_topic = "{}/job/run".format(self.base_topic)
        events_topic = "{}/events".format(self.base_topic)

        # 1. Status Sensor
        status_obj = "status"
        status_topic_cfg = "{}/sensor/{}/{}/config".format(self.ha_prefix, self.device_id, status_obj)
        status_payload = {
            "name": "{} Status".format(self.device_name),
            "unique_id": "{}_{}".format(self.device_id, status_obj),
            "state_topic": status_topic,
            "value_template": "{{ value_json.state }}",
            "json_attributes_topic": status_topic,
            "availability_topic": avail_topic,
            "icon": "mdi:chip",
            "device": self.device_info
        }
        messages.append((status_topic_cfg, status_payload))

        # 2. Emergency Abort Button
        abort_obj = "abort_button"
        abort_topic_cfg = "{}/button/{}/{}/config".format(self.ha_prefix, self.device_id, abort_obj)
        abort_payload = {
            "name": "{} Abort Job".format(self.device_name),
            "unique_id": "{}_{}".format(self.device_id, abort_obj),
            "command_topic": abort_topic,
            "payload_press": json.dumps({"reason": "ha_abort_button"}),
            "availability_topic": avail_topic,
            "icon": "mdi:alert-octagon",
            "device": self.device_info
        }
        messages.append((abort_topic_cfg, abort_payload))

        # 3. Last Event Sensor (e.g. fingerprint scan events)
        event_obj = "last_event"
        event_topic_cfg = "{}/sensor/{}/{}/config".format(self.ha_prefix, self.device_id, event_obj)
        event_payload = {
            "name": "{} Last Event".format(self.device_name),
            "unique_id": "{}_{}".format(self.device_id, event_obj),
            "state_topic": events_topic,
            "value_template": "{{ value_json.event }}",
            "json_attributes_topic": events_topic,
            "availability_topic": avail_topic,
            "icon": "mdi:fingerprint",
            "device": self.device_info
        }
        messages.append((event_topic_cfg, event_payload))

        # 4. Buttons for pre-configured routines
        routines = self.config.routines
        for routine_name in routines.keys():
            obj_id = "routine_{}".format(routine_name)
            disc_topic = "{}/button/{}/{}/config".format(self.ha_prefix, self.device_id, obj_id)
            nice_name = routine_name.replace("_", " ").title()
            btn_payload = {
                "name": "{} Run {}".format(self.device_name, nice_name),
                "unique_id": "{}_{}".format(self.device_id, obj_id),
                "command_topic": run_topic,
                "payload_press": json.dumps({"job": routine_name}),
                "availability_topic": avail_topic,
                "icon": "mdi:play-circle-outline",
                "device": self.device_info
            }
            if routine_name == "door_unlock":
                btn_payload["icon"] = "mdi:door-open"
            elif routine_name == "door_lock":
                btn_payload["icon"] = "mdi:door-closed"
            messages.append((disc_topic, btn_payload))

        return messages

    async def publish_discovery(self, mqtt_client):
        """Publish all discovery messages via the MQTT client."""
        for topic, payload in self.generate_discovery_messages():
            await mqtt_client.publish(topic, payload, retain=True)
