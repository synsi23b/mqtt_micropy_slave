"""Resilient MQTT client wrapper supporting MicroPython umqtt and desktop fallback."""
import json

try:
    # pyrefly: ignore [missing-import]
    import uasyncio as asyncio
except ImportError:
    import asyncio


class MQTTClientWrapper:
    """Async MQTT manager with LWT, reconnect loop, and topic subscriptions."""

    def __init__(self, config, on_message_cb=None):
        self.config = config
        self.on_message_cb = on_message_cb
        self.device_id = config.device_id
        self.base_topic = config.base_topic
        self.client = None
        self._is_connected = False
        self._is_desktop = False

        # Precompute standard topics
        self.topic_availability = "{}/availability".format(self.base_topic)
        self.topic_status = "{}/status".format(self.base_topic)
        self.topic_events = "{}/events".format(self.base_topic)
        self.topic_job_run = "{}/job/run".format(self.base_topic)
        self.topic_job_abort = "{}/job/abort".format(self.base_topic)
        self.topic_ota_check = "{}/ota/check".format(self.base_topic)
        self.topic_ota_update = "{}/ota/update".format(self.base_topic)

    @property
    def is_connected(self):
        return self._is_connected

    def _create_client(self):
        # Try MicroPython umqtt first
        try:
            # pyrefly: ignore [missing-import]
            from umqtt.simple import MQTTClient
            client = MQTTClient(
                client_id=self.device_id,
                server=self.config.mqtt_host,
                port=self.config.mqtt_port,
                user=self.config.mqtt_user or None,
                password=self.config.mqtt_password or None,
                keepalive=self.config.get("mqtt", "keepalive", default=60)
            )
            client.set_last_will(self.topic_availability, b"offline", retain=True, qos=1)
            client.set_callback(self._on_umqtt_msg)
            self._is_desktop = False
            return client
        except ImportError:
            pass

        # Fallback to desktop paho-mqtt
        try:
            import paho.mqtt.client as paho
            # Handle paho v1 vs v2 callback_api_version
            try:
                client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=self.device_id)
            except (AttributeError, TypeError):
                client = paho.Client(client_id=self.device_id)

            if self.config.mqtt_user:
                client.username_pw_set(self.config.mqtt_user, self.config.mqtt_password)

            client.will_set(self.topic_availability, payload="offline", qos=1, retain=True)

            def _on_connect(c, userdata, flags, rc, properties=None):
                if rc == 0 or (hasattr(rc, "value") and rc.value == 0):
                    self._is_connected = True
                    c.subscribe(self.topic_job_run)
                    c.subscribe(self.topic_job_abort)
                    c.subscribe(self.topic_ota_check)
                    c.subscribe(self.topic_ota_update)
                    c.publish(self.topic_availability, "online", retain=True, qos=1)

            def _on_disconnect(c, userdata, *args):
                self._is_connected = False

            def _on_paho_msg(c, userdata, msg):
                self._dispatch_message(msg.topic, msg.payload.decode("utf-8", "ignore"))

            client.on_connect = _on_connect
            client.on_disconnect = _on_disconnect
            client.on_message = _on_paho_msg
            self._is_desktop = True
            return client
        except ImportError:
            # In-memory mock client
            self._is_desktop = False
            return None

    def _on_umqtt_msg(self, topic, msg):
        topic_str = topic.decode("utf-8") if isinstance(topic, bytes) else str(topic)
        payload_str = msg.decode("utf-8") if isinstance(msg, bytes) else str(msg)
        self._dispatch_message(topic_str, payload_str)

    def _dispatch_message(self, topic, payload):
        if self.on_message_cb:
            asyncio.create_task(self.on_message_cb(topic, payload))

    async def connect(self):
        """Establish MQTT connection and subscribe to command topics."""
        if not self.client:
            self.client = self._create_client()

        if not self.client:
            print("[MQTT] Mock/Dummy client active")
            self._is_connected = True
            return True

        print("[MQTT] Connecting to broker {}:{}...".format(self.config.mqtt_host, self.config.mqtt_port))

        if self._is_desktop:
            try:
                self.client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
                self.client.loop_start()
                # Wait briefly for connection callback
                for _ in range(30):
                    if self._is_connected:
                        break
                    await asyncio.sleep_ms(100)
                return self._is_connected
            except Exception as e:
                print("[MQTT] Desktop connection error:", e)
                return False
        else:
            # MicroPython umqtt
            try:
                self.client.connect()
                self._is_connected = True
                # Subscribe to topics
                self.client.subscribe(self.topic_job_run.encode("utf-8"))
                self.client.subscribe(self.topic_job_abort.encode("utf-8"))
                self.client.subscribe(self.topic_ota_check.encode("utf-8"))
                self.client.subscribe(self.topic_ota_update.encode("utf-8"))
                # Publish LWT online status
                self.client.publish(self.topic_availability.encode("utf-8"), b"online", retain=True, qos=1)
                print("[MQTT] Connected and subscribed successfully.")
                return True
            except Exception as e:
                print("[MQTT] MicroPython connection failed:", e)
                self._is_connected = False
                return False

    async def disconnect(self):
        if not self.client or not self._is_connected:
            return
        try:
            await self.publish(self.topic_availability, "offline", retain=True, qos=1)
            if self._is_desktop:
                self.client.loop_stop()
                self.client.disconnect()
            else:
                self.client.disconnect()
        except Exception:
            pass
        finally:
            self._is_connected = False

    async def publish(self, topic, payload, retain=False, qos=0):
        """Publish payload (dict, str, or bytes) to MQTT topic."""
        if isinstance(payload, dict):
            payload = json.dumps(payload)

        if not self._is_connected or not self.client:
            return

        try:
            if self._is_desktop:
                self.client.publish(topic, payload, qos=qos, retain=retain)
            else:
                t_bytes = topic.encode("utf-8") if isinstance(topic, str) else topic
                p_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
                self.client.publish(t_bytes, p_bytes, retain=retain, qos=qos)
        except Exception as e:
            print("[MQTT] Publish error:", e)

    async def check_messages(self):
        """Poll for incoming messages (relevant for MicroPython umqtt)."""
        if not self._is_desktop and self.client and self._is_connected:
            try:
                # Non-blocking check msg
                self.client.check_msg()
            except Exception as e:
                print("[MQTT] check_msg error:", e)
                self._is_connected = False

    async def publish_status(self, status_dict):
        await self.publish(self.topic_status, status_dict)

    async def publish_event(self, event_type, details):
        payload = {"event": event_type, "timestamp": asyncio.current_task().get_name() if hasattr(asyncio.current_task(), "get_name") else None}
        if isinstance(details, dict):
            payload.update(details)
        else:
            payload["details"] = details
        await self.publish(self.topic_events, payload)

    async def maintain_loop(self):
        """Background task for message polling and periodic heartbeats."""
        heartbeat_s = self.config.heartbeat_interval
        last_heartbeat = 0
        while True:
            if not self._is_connected:
                print("[MQTT] Disconnected. Attempting reconnect...")
                await self.connect()
                await asyncio.sleep(5)
                continue

            await self.check_messages()

            last_heartbeat += 0.2
            if last_heartbeat >= heartbeat_s:
                last_heartbeat = 0
                await self.publish_status({"state": "online", "type": "heartbeat"})

            await asyncio.sleep_ms(200)
