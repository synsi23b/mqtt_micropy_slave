#!/usr/bin/env python3
"""
Deployment, credential injection, and management CLI for ESP32 MicroPython Slave.
Uses mpremote under the hood to sync files, inject secrets, and monitor serial output.
"""
import argparse
import json
import os
import subprocess
import sys

CONFIG_FILE = "config.json"
CONFIG_EXAMPLE = "config.example.json"


def load_or_create_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    elif os.path.exists(CONFIG_EXAMPLE):
        with open(CONFIG_EXAMPLE, "r") as f:
            return json.load(f)
    else:
        return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"[OK] Configuration saved to {CONFIG_FILE} (git-ignored)")


def cmd_set_credentials(args):
    cfg = load_or_create_config()
    if "wifi" not in cfg:
        cfg["wifi"] = {}
    if "mqtt" not in cfg:
        cfg["mqtt"] = {}
    if "device" not in cfg:
        cfg["device"] = {}

    if args.ssid is not None:
        cfg["wifi"]["ssid"] = args.ssid
    if args.wifi_pass is not None:
        cfg["wifi"]["password"] = args.wifi_pass
    if args.mqtt_host is not None:
        cfg["mqtt"]["host"] = args.mqtt_host
    if args.mqtt_port is not None:
        cfg["mqtt"]["port"] = args.mqtt_port
    if args.mqtt_user is not None:
        cfg["mqtt"]["user"] = args.mqtt_user
    if args.mqtt_pass is not None:
        cfg["mqtt"]["password"] = args.mqtt_pass
    if args.device_id is not None:
        cfg["device"]["id"] = args.device_id
        if not cfg.get("mqtt", {}).get("base_topic"):
            cfg["mqtt"]["base_topic"] = f"slave/{args.device_id}"

    save_config(cfg)
    print("Credentials updated successfully:")
    print(f"  Device ID: {cfg.get('device', {}).get('id')}")
    print(f"  Wi-Fi SSID: {cfg.get('wifi', {}).get('ssid')}")
    print(f"  MQTT Host: {cfg.get('mqtt', {}).get('host')}:{cfg.get('mqtt', {}).get('port')}")


def run_mpremote(port, *cmd_args):
    base = [sys.executable, "-m", "mpremote"]
    if port:
        base.extend(["connect", port])
    full_cmd = base + list(cmd_args)
    print(f"Running: {' '.join(full_cmd)}")
    return subprocess.run(full_cmd)


def cmd_sync(args):
    if not os.path.exists(CONFIG_FILE):
        print(f"[ERROR] {CONFIG_FILE} does not exist. Run 'deploy.py set-credentials' first!")
        sys.exit(1)

    port = args.port
    print(f"[1/3] Ensuring directories on ESP32 ({port or 'auto'})...")
    run_mpremote(port, "fs", "mkdir", "drivers")
    run_mpremote(port, "fs", "mkdir", "engine")
    run_mpremote(port, "fs", "mkdir", "net")
    run_mpremote(port, "fs", "mkdir", "ha")

    print("[2/3] Uploading source files...")
    # Sync src directory contents
    for root, dirs, files in os.walk("src"):
        for file in files:
            if file.endswith(".py"):
                local_path = os.path.join(root, file).replace("\\", "/")
                rel_path = os.path.relpath(local_path, "src").replace("\\", "/")
                remote_path = f":{rel_path}"
                print(f"  Copying {local_path} -> {remote_path}")
                run_mpremote(port, "fs", "cp", local_path, remote_path)

    print("[3/3] Uploading config.json...")
    run_mpremote(port, "fs", "cp", CONFIG_FILE, ":config.json")
    print("\n[SUCCESS] Sync complete! Restarting board...")
    run_mpremote(port, "reset")


def cmd_monitor(args):
    run_mpremote(args.port, "repl")


def cmd_ls(args):
    run_mpremote(args.port, "fs", "ls")


def main():
    parser = argparse.ArgumentParser(description="ESP32 MicroPython Slave Deployer & Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # set-credentials
    p_cred = subparsers.add_parser("set-credentials", help="Set Wi-Fi and MQTT credentials in config.json")
    p_cred.add_argument("--ssid", help="Wi-Fi SSID")
    p_cred.add_argument("--wifi-pass", help="Wi-Fi Password")
    p_cred.add_argument("--mqtt-host", help="MQTT Broker Host / IP")
    p_cred.add_argument("--mqtt-port", type=int, default=1883, help="MQTT Broker Port")
    p_cred.add_argument("--mqtt-user", help="MQTT Username (optional)")
    p_cred.add_argument("--mqtt-pass", help="MQTT Password (optional)")
    p_cred.add_argument("--device-id", help="Device ID")
    p_cred.set_defaults(func=cmd_set_credentials)

    # sync
    p_sync = subparsers.add_parser("sync", help="Upload firmware and config.json to ESP32")
    p_sync.add_argument("--port", help="Serial port (e.g. COM3 or /dev/ttyUSB0). Default: auto-detect")
    p_sync.set_defaults(func=cmd_sync)

    # monitor
    p_mon = subparsers.add_parser("monitor", help="Open serial REPL monitor on ESP32")
    p_mon.add_argument("--port", help="Serial port")
    p_mon.set_defaults(func=cmd_monitor)

    # ls
    p_ls = subparsers.add_parser("ls", help="List files on ESP32 flash filesystem")
    p_ls.add_argument("--port", help="Serial port")
    p_ls.set_defaults(func=cmd_ls)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
