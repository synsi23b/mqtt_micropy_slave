"""Live integration test against Eclipse-Mosquitto MQTT broker."""
import pytest
import asyncio
import os
import socket
from config import Config
from net.mqtt import MQTTClientWrapper

BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", os.environ.get("MQTT_HOST", "127.0.0.1"))
BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", os.environ.get("MQTT_PORT", "1883")))


def is_broker_running(host=BROKER_HOST, port=BROKER_PORT):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not is_broker_running(),
    reason=f"Mosquitto broker not reachable at {BROKER_HOST}:{BROKER_PORT}. Set MQTT_BROKER_HOST=<ip> to target remote broker."
)


@pytest.mark.asyncio
async def test_live_mqtt_lwt_and_messaging():
    cfg = Config({
        "device": {"id": "esp32_integration_test"},
        "mqtt": {"host": BROKER_HOST, "port": BROKER_PORT, "base_topic": "slave/integration_test"}
    })

    received_messages = []

    async def on_msg(topic, payload):
        received_messages.append((topic, payload))

    client = MQTTClientWrapper(cfg, on_message_cb=on_msg)
    connected = await client.connect()
    assert connected is True, f"Failed to connect to MQTT broker at {BROKER_HOST}:{BROKER_PORT}"

    # Publish status
    await client.publish_status({"state": "idle", "test": True})
    await asyncio.sleep(0.3)

    # Publish command to self
    await client.publish(client.topic_job_run, {"job": "door_unlock"})
    await asyncio.sleep(0.5)

    # Verify message dispatch
    assert any(topic == client.topic_job_run for topic, _ in received_messages)

    await client.disconnect()
