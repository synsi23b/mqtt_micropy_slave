"""Main application entrypoint and uasyncio supervisor."""
import json
import gc

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

from config import Config
from drivers.solenoid import SolenoidLock
from drivers.as608 import AS608
from engine.actions import ActionRegistry, ActionExecutionContext
from engine.job_runner import JobRunner
from net.wifi import WiFiManager
from net.mqtt import MQTTClientWrapper
from ha.discovery import HADiscovery


class SlaveApplication:
    """Core application lifecycle and hardware coordinator."""

    def __init__(self, config_path="config.json"):
        self.config = Config.load_from_file(config_path)
        self.wifi = None
        self.mqtt = None
        self.solenoid = None
        self.as608 = None
        self.pins = {}
        self.registry = None
        self.context = None
        self.job_runner = None
        self.ha = None
        self._bg_tasks = []

    def init_hardware(self):
        """Initialize GPIO, Solenoid, and AS608 UART."""
        # Initialize Solenoid
        sol_cfg = self.config.get("hardware", "solenoid", default={})
        sol_pin = sol_cfg.get("pin", 23)
        active_high = sol_cfg.get("active_high", True)
        default_pulse = sol_cfg.get("default_pulse_ms", 3000)
        max_pulse = sol_cfg.get("max_pulse_ms", 10000)
        self.solenoid = SolenoidLock(
            pin_num=sol_pin,
            active_high=active_high,
            default_pulse_ms=default_pulse,
            max_pulse_ms=max_pulse
        )

        # Initialize AS608 if configured
        as608_cfg = self.config.get("hardware", "as608", default={})
        uart_id = as608_cfg.get("uart_id", 2)
        tx_pin = as608_cfg.get("tx_pin", 17)
        rx_pin = as608_cfg.get("rx_pin", 16)
        baudrate = as608_cfg.get("baudrate", 57600)
        pwd = as608_cfg.get("password", 0)
        addr = as608_cfg.get("address", 0xFFFFFFFF)

        try:
            from machine import UART
            uart = UART(uart_id, baudrate=baudrate, tx=tx_pin, rx=rx_pin)
            self.as608 = AS608(uart, address=addr, password=pwd)
            print("[Init] AS608 UART initialized on port", uart_id)
        except Exception as e:
            print("[Init] AS608 hardware UART unavailable ({}); using mock/none".format(e))
            self.as608 = None

        # Build context & registry
        self.registry = ActionRegistry()
        self.context = ActionExecutionContext(
            config=self.config,
            pins=self.pins,
            solenoid=self.solenoid,
            as608=self.as608,
            mqtt_publish_cb=self._on_action_mqtt_publish,
            event_cb=self._on_action_event
        )

        # Job Runner
        self.job_runner = JobRunner(
            action_registry=self.registry,
            context=self.context,
            status_callback=self._on_job_status_change
        )

        # Wi-Fi & MQTT
        self.wifi = WiFiManager(
            ssid=self.config.wifi_ssid,
            password=self.config.wifi_password
        )
        self.mqtt = MQTTClientWrapper(
            config=self.config,
            on_message_cb=self._on_mqtt_message
        )
        self.ha = HADiscovery(self.config)

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
                print("[MQTT Dispatch] Job completed with:", res)

            elif topic == self.mqtt.topic_job_abort:
                data = json.loads(payload) if payload else {}
                reason = data.get("reason", "mqtt_abort_command")
                res = await self.job_runner.abort(reason=reason)
                print("[MQTT Dispatch] Job abort executed:", res)

        except Exception as e:
            print("[MQTT Dispatch] Exception processing message:", e)

    async def background_fingerprint_listener(self):
        """Continuously check for fingerprint touch when system is idle."""
        if not self.as608:
            return

        print("[AS608 Listener] Started background scan listener")
        while True:
            try:
                # Only scan if job runner is not currently executing another job
                if not self.job_runner.is_busy:
                    found, page_id, score, err = await self.as608.search_once()
                    if found:
                        print("[AS608 Listener] Finger matched! Slot: {}, Score: {}".format(page_id, score))
                        await self.mqtt.publish_event("fingerprint_scanned", {
                            "finger_id": page_id,
                            "confidence": score
                        })
                        # Wait for finger lift to avoid duplicate rapid events
                        await self.as608.wait_finger_lift(timeout_s=5)
                await asyncio.sleep_ms(200)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep_ms(1000)

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
        t_as608 = asyncio.create_task(self.background_fingerprint_listener())

        self._bg_tasks = [t_wifi, t_mqtt, t_as608]

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
