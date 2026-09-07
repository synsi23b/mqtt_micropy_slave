#!/usr/bin/env python3
"""
Desktop simulator for ESP32 MicroPython Slave.
Runs the complete firmware stack natively on desktop Python, connecting to
a local or remote Mosquitto MQTT broker for testing without physical hardware.
"""
import asyncio
import os
import sys

# Ensure src/ is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

# Ensure mock machine is loaded
import tests.conftest

from main import SlaveApplication


async def run_simulator():
    print("==================================================")
    print("       ESP32 MicroPython Slave - Desktop Simulator")
    print("==================================================")

    config_path = "config.json" if os.path.exists("config.json") else "config.example.json"
    print(f"Loading configuration from: {config_path}")

    app = SlaveApplication(config_path=config_path)
    try:
        await app.start()
    except KeyboardInterrupt:
        print("\nStopping simulator...")
        app.job_runner.emergency_stop()


if __name__ == "__main__":
    try:
        asyncio.run(run_simulator())
    except KeyboardInterrupt:
        print("\nSimulator terminated.")
