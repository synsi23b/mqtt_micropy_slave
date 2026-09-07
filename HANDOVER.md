# Project Handover & Context

This document captures the complete architectural state, design decisions, and testing status of `mqtt_micropy_slave` for seamless continuation on Linux.

## 1. Project Goal & Architecture
A production-ready MicroPython MQTT edge slave for ESP32 devices designed to execute physical routines defined by Home Assistant (or an external orchestrator).

### Key Subsystems:
1. **Dynamic Hardware Component Registry (`src/engine/components.py`)**:
   - Zero hardcoded pins. Components are declared dynamically in `config.json`:
     - `solenoid`: Door lock bolt / failsafe release.
     - `servo`: SwitchBot-style robotic presser.
     - `digital_in`: Appliance status LED reader or reed sensor (with invert, debounce, and live change reporting).
     - `digital_out`: Status LEDs, buzzers, relays.
     - `analog_in`: ADC voltage/sensor reader with smoothing.
     - `as608`: Optical UART fingerprint scanner.
2. **Modular Actions & Job Engine (`src/engine/actions.py`, `src/engine/job_runner.py`)**:
   - Generic actions targeting components by name (`pulse`, `digital_write`, `delay`, `servo_set`, `read`, `as608_search_event`, `as608_enroll_step`).
   - Concurrency locking with rejection of overlapping jobs.
   - Emergency abort (`slave/<id>/abort`) that resets all outputs to failsafe off.
3. **Interactive 5-Sample Fingerprint Protocol (`src/drivers/as608.py`)**:
   - `enroll_step` takes sample index 1..5.
   - Finger-lift verification ensures 5 distinct physical scans.
   - Designed for orchestration via external smartphone web app with buzzer/LED feedback.
4. **Wi-Fi Over-The-Air (OTA) Updates (`src/net/ota.py`, `tools/ota_manager.py`)**:
   - Version tracking via `src/version.json`.
   - Release packaging with SHA-256 manifest generator (`tools/ota_manager.py package --version X.Y.Z`).
   - SCP publisher to remote Nginx host (`tools/ota_manager.py publish --host user@host --path /var/www/firmware`).
   - MicroPython client fetches manifest, stages files as `.new`, replaces atomically, and auto-rolls back on error.
   - HA Discovery button "Check & Apply OTA" and MQTT topic `slave/<id>/ota/update`.
5. **Home Assistant Auto-Discovery (`src/ha/discovery.py`)**:
   - Generates discovery payloads for status sensor, routine buttons, abort button, OTA update button, and binary sensors for monitored digital inputs.

---

## 2. Linux Setup & Quickstart

```bash
# 1. Create virtual environment (use python3 -m venv or virtualenv)
virtualenv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run all tests (28 passing, 1 skipped for hardware HIL)
pytest -v tests/
```

### Integration Broker & Simulator:
```bash
docker compose up -d mosquitto
pytest -v tests/integration/

# Run native desktop simulation
python tools/simulator.py
```

---

## 3. Test Suite Summary
- `tests/test_components.py`: Validates door, switchbot, and appliance LED profiles.
- `tests/test_digital_io.py`: Validates input inversion, debouncing, and output pulse.
- `tests/test_as608.py`: Validates packet checksums, password auth, search, slot delete, and enrollment steps.
- `tests/test_actions.py`: Validates pulse, servo, digital write, and search actions.
- `tests/test_job_runner.py`: Validates routine execution, concurrency locking, and abort safety.
- `tests/test_ha_discovery.py`: Validates dynamic MQTT discovery payload structure.
- `tests/test_ota.py`: Validates semantic versioning, staging, atomic replacement, and rollback.
- `tests/integration/test_door_flow.py`: End-to-end simulation of fingerprint scan -> auth -> solenoid unlock.
- `tests/integration/test_mqtt_integration.py`: Live Mosquitto LWT, publish/subscribe, and thread-safe async dispatching.
- `tests/hil/test_pi_hil.py`: Hardware-in-the-loop pulse timing verification (skipped unless running on Raspberry Pi).
