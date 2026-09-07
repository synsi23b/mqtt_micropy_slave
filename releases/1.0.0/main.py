"""Main application entrypoint and uasyncio supervisor with dynamic components and OTA."""
import json
import gc

try:
    # pyrefly: ignore [missing-import]
    import uasyncio as asyncio
except ImportError:
    import asyncio

from config import Config
from engine.components import ComponentManager
from engine.actions import ActionRegistry, ActionExecutionContext
from engine.job_runner import JobRunner
from net.wifi import WiFiManager
from net.mqtt import MQTTClientWrapper
from net.ota import OTAClient
from ha.discovery import HADiscovery


class SlaveApplication:
    """Core application lifecycle and dynamic hardware coordinator."""

    def __init__(self, config_path="config.json"):
        self.config = Config.load_from_file(config_path)
        self.components = None
        self.wifi = None
        self.mqtt = None
        self.ota = None
        self.registry = None
        self.context = None
        self.job_runner = None
        self.ha = None
        self._bg_tasks = []

    def init_hardware(self):
        """Dynamically instantiate hardware components declared in config."""
        print("[Init] Loading components:", list(self.config.components.keys()))
        self.components = ComponentManager(self.config.components)

        # Build context & registry
        self.registry = ActionRegistry()
        self.context = ActionExecutionContext(
            config=self.config,
            components=self.components,
            mqtt_publish_cb=self._on_action_mqtt_publish,
            event_cb=self._on_action_event
        )

        # Job Runner
        self.job_runner = JobRunner(
            action_registry=self.registry,
            context=self.context,
            status_callback=self._on_job_status_change
        )

        # Wi-Fi, MQTT & OTA
        self.wifi = WiFiManager(
            ssid=self.config.wifi_ssid,
            password=self.config.wifi_password
        )
        self.mqtt = MQTTClientWrapper(
            config=self.config,
            on_message_cb=self._on_mqtt_message
        )
        self.ha = HADiscovery(self.config, component_manager=self.components)
        self.ota = OTAClient(manifest_url=self.config.ota_manifest_url)

    async def _on_action_mqtt_publish(self, topic, payload):
        if self.mqtt:
            await self.mqtt.publish(topic, payload)

    async def _on_action_event(self, event_type, details):
        if self.mqtt:
            await self.mqtt.publish_event(event_type, details)

    async def _on_job_status_change(self, status_dict):
        if self.mqtt:
            await self.mqtt.publish_status(status_dict)

    async def _on_mqtt_message(self, topic, payload):
        """Route incoming MQTT command messages."""
        print("[MQTT Dispatch] Topic: {}, Payload: {}".format(topic, payload))
        try:
            if topic == self.mqtt.topic_job_run:
                data = json.loads(payload) if payload else {}
                job = data.get("job")
                steps = data.get("steps")
                params = data.get("params")
                job_id = data.get("job_id")

                job_spec = steps if steps else job
                if not job_spec:
                    print("[MQTT Dispatch] Error: neither 'job' nor 'steps' provided")
                    return

                res = await self.job_runner.run_job(job_spec, params=params, job_id=job_id)
                print("[MQTT Dispatch] Job completed:", res)

            elif topic == self.mqtt.topic_job_abort:
                data = json.loads(payload) if payload else {}
                reason = data.get("reason", "mqtt_abort_command")
                res = await self.job_runner.abort(reason=reason)
                print("[MQTT Dispatch] Job abort executed:", res)

            elif topic == self.mqtt.topic_ota_check:
                has_upd, rem_ver, manifest, err = await self.ota.check_update()
                await self.mqtt.publish_event("ota_check_result", {
                    "has_update": has_upd,
                    "latest_version": rem_ver,
                    "current_version": self.ota.current_version,
                    "error": err
                })

            elif topic == self.mqtt.topic_ota_update:
                await self.mqtt.publish_status({"state": "updating_ota"})
                ok, res = await self.ota.apply_update(auto_reboot=True)
                await self.mqtt.publish_event("ota_update_result", {
                    "success": ok,
                    "result": res
                })

        except Exception as e:
            print("[MQTT Dispatch] Exception processing message:", e)

    async def background_input_listener(self):
        """Monitor digital inputs (status LEDs, sensors) and publish state changes."""
        reporting_inputs = self.components.get_reporting_inputs()
        if not reporting_inputs:
            return

        print("[Input Listener] Monitoring {} reporting inputs".format(len(reporting_inputs)))
        while True:
            try:
                for comp_id, comp in reporting_inputs:
                    changed, new_state = await comp.check_change()
                    if changed:
                        topic = "{}/input/{}".format(self.config.base_topic, comp_id)
                        print("[Input] {} state changed to {}".format(comp_id, new_state))
                        await self.mqtt.publish(topic, str(new_state), retain=True)
                        await self.mqtt.publish_event("input_changed", {
                            "component": comp_id,
                            "state": new_state
                        })
                await asyncio.sleep_ms(100)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep_ms(1000)

    async def background_fingerprint_listener(self):
        """Continuously check for fingerprint touch if AS608 is configured."""
        as608_comp = self.context.as608
        if not as608_comp:
            return

        print("[AS608 Listener] Started background scan listener")
        while True:
            try:
                if not self.job_runner.is_busy:
                    found, page_id, score, err = await as608_comp.search_once()
                    if found:
                        print("[AS608] Finger matched! Slot: {}, Score: {}".format(page_id, score))
                        await self.mqtt.publish_event("fingerprint_scanned", {
                            "finger_id": page_id,
                            "confidence": score
                        })
                        await as608_comp.wait_finger_lift(timeout_s=5)
                await asyncio.sleep_ms(200)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep_ms(1000)

    async def background_ota_supervisor(self):
        """Periodic OTA check supervisor."""
        if not self.config.ota_enabled or not self.config.ota_manifest_url:
            return

        # Check on boot after delay
        if self.config.ota_check_on_boot:
            await asyncio.sleep(10)
            has_upd, rem_ver, _, _ = await self.ota.check_update()
            if has_upd:
                print("[OTA] New version {} detected on boot. Updating...".format(rem_ver))
                await self.ota.apply_update(auto_reboot=True)

        interval_s = self.config.ota_check_interval
        while True:
            await asyncio.sleep(interval_s)
            has_upd, rem_ver, _, _ = await self.ota.check_update()
            if has_upd:
                print("[OTA] New version {} detected during periodic check. Updating...".format(rem_ver))
                await self.ota.apply_update(auto_reboot=True)

    async def start(self):
        """Start all async supervisors."""
        print("[App] Starting MicroPython Edge Slave...")
        self.init_hardware()

        # Connect Wi-Fi
        await self.wifi.connect()

        # Connect MQTT
        mqtt_ok = await self.mqtt.connect()
        if mqtt_ok:
            # Publish HA Discovery entities
            await self.ha.publish_discovery(self.mqtt)

        # Launch background tasks
        t_wifi = asyncio.create_task(self.wifi.maintain_connection())
        t_mqtt = asyncio.create_task(self.mqtt.maintain_loop())
        t_inputs = asyncio.create_task(self.background_input_listener())
        t_as608 = asyncio.create_task(self.background_fingerprint_listener())
        t_ota = asyncio.create_task(self.background_ota_supervisor())

        self._bg_tasks = [t_wifi, t_mqtt, t_inputs, t_as608, t_ota]

        try:
            while True:
                gc.collect()
                await asyncio.sleep(10)
        finally:
            self.job_runner.emergency_stop()


def run():
    app = SlaveApplication()
    asyncio.run(app.start())


if __name__ == "__main__":
    run()
