#!/usr/bin/env python3
"""
Hardware-in-the-Loop (HIL) Raspberry Pi Manager & Remote Test Runner.

Provides automated provisioning, SSH key management, file synchronization,
and remote test execution for the ESP32 HIL test rig.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys

CONFIG_FILE = ".hil_config.json"
ENV_FILE = ".env"
DEFAULT_USER = "pi"
DEFAULT_PORT = 22
DEFAULT_DEVICE_ID = "esp32_hil_dut"


def get_default_key_path():
    """Find existing default SSH private key or propose an ed25519 key."""
    home = os.path.expanduser("~")
    for key_name in ["id_ed25519", "id_rsa"]:
        p = os.path.join(home, ".ssh", key_name)
        if os.path.exists(p):
            return p
    return os.path.join(home, ".ssh", "id_ed25519")


def load_hil_config():
    """Load configuration from .hil_config.json or .env."""
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
        except Exception:
            pass

    # Merge or fallback to environment / .env
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "HIL_PI_HOST" and "host" not in config:
                    config["host"] = v
                elif k == "HIL_PI_USER" and "user" not in config:
                    config["user"] = v
                elif k == "HIL_PI_KEY" and "key_file" not in config:
                    config["key_file"] = v
                elif k == "HIL_PI_PORT" and "port" not in config:
                    try:
                        config["port"] = int(v)
                    except ValueError:
                        pass
                elif k == "HIL_DEVICE_ID" and "device_id" not in config:
                    config["device_id"] = v

    # Fallbacks from active os.environ
    if "HIL_PI_HOST" in os.environ:
        config["host"] = os.environ["HIL_PI_HOST"]
    if "HIL_PI_USER" in os.environ:
        config["user"] = os.environ["HIL_PI_USER"]
    if "HIL_PI_KEY" in os.environ:
        config["key_file"] = os.environ["HIL_PI_KEY"]
    if "HIL_DEVICE_ID" in os.environ:
        config["device_id"] = os.environ["HIL_DEVICE_ID"]

    return config


def save_hil_config(config):
    """Persist settings to .hil_config.json and update .env."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    # Update or append to .env
    env_lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            env_lines = f.readlines()

    keys_to_update = {
        "HIL_PI_HOST": config.get("host", ""),
        "HIL_PI_USER": config.get("user", DEFAULT_USER),
        "HIL_PI_PORT": str(config.get("port", DEFAULT_PORT)),
        "HIL_PI_KEY": config.get("key_file", ""),
        "HIL_DEVICE_ID": config.get("device_id", DEFAULT_DEVICE_ID),
    }

    updated_keys = set()
    new_lines = []
    for line in env_lines:
        trimmed = line.strip()
        matched = False
        for k, v in keys_to_update.items():
            if trimmed.startswith(f"{k}=") or trimmed == k:
                new_lines.append(f"{k}={v}\n")
                updated_keys.add(k)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for k, v in keys_to_update.items():
        if k not in updated_keys and v:
            new_lines.append(f"{k}={v}\n")

    with open(ENV_FILE, "w") as f:
        f.writelines(new_lines)


def is_port_open(host, port, timeout=2.0):
    """Fast socket check to see if remote host is reachable on SSH port."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def ensure_ssh_key(key_path):
    """Ensure an SSH private/public keypair exists, generating one if necessary."""
    key_path = os.path.expanduser(key_path)
    pub_path = key_path + ".pub"

    if os.path.exists(key_path) and os.path.exists(pub_path):
        return key_path, pub_path

    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    print(f"[KeyGen] Generating new ED25519 SSH keypair at {key_path}...")
    cmd = ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key_path, "-C", "esp32_hil_runner"]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print("[KeyGen Error] Failed to generate SSH key")
        sys.exit(res.returncode)
    return key_path, pub_path


def test_ssh_connection(host, user, key_path, port=22):
    """Test if passwordless keyed SSH is currently working."""
    key_path = os.path.expanduser(key_path)
    cmd = [
        "ssh",
        "-i", key_path,
        "-p", str(port),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=4",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{user}@{host}",
        "echo OK"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0 and "OK" in res.stdout


def install_ssh_key(host, user, pub_key_path, port=22):
    """Copy public key to Raspberry Pi using ssh-copy-id."""
    print(f"\n[SSH Setup] Installing public key {pub_key_path} to {user}@{host}...")
    print("You will be prompted for the Raspberry Pi's user password once:\n")

    cmd = ["ssh-copy-id", "-i", pub_key_path, "-p", str(port), f"{user}@{host}"]
    res = subprocess.run(cmd)
    return res.returncode == 0


def run_remote_command(host, user, key_path, command, port=22, check=True):
    """Run a shell command on the remote Pi over SSH."""
    key_path = os.path.expanduser(key_path)
    ssh_cmd = [
        "ssh",
        "-i", key_path,
        "-p", str(port),
        "-o", "StrictHostKeyChecking=accept-new",
        f"{user}@{host}",
        command
    ]
    return subprocess.run(ssh_cmd, check=check)


def cmd_setup(args):
    """Full automated provisioning of Raspberry Pi."""
    host = args.host
    user = args.user or DEFAULT_USER
    port = args.port or DEFAULT_PORT
    key_path = os.path.expanduser(args.key or get_default_key_path())
    device_id = args.device_id or DEFAULT_DEVICE_ID

    print("==================================================")
    print("      Raspberry Pi HIL Automated Provisioning     ")
    print("==================================================")
    print(f"Target Pi:   {user}@{host}:{port}")
    print(f"SSH Key:     {key_path}")
    print(f"DUT ID:      {device_id}")

    # 1. Connectivity Check
    print(f"\n[1/5] Checking connectivity to {host}:{port}...")
    if not is_port_open(host, port, timeout=4.0):
        print(f"[ERROR] Cannot reach {host} on port {port}. Ensure the Pi is powered on and joined to your WLAN.")
        sys.exit(1)
    print("✓ Host is online and accepting connections.")

    # 2. SSH Key Setup
    print("\n[2/5] Verifying SSH authentication...")
    priv_key, pub_key = ensure_ssh_key(key_path)

    if test_ssh_connection(host, user, priv_key, port):
        print("✓ Keyed passwordless SSH already configured and active.")
    else:
        print("Passwordless SSH not yet set up.")
        if not install_ssh_key(host, user, pub_key, port):
            print("[ERROR] Failed to install SSH key via ssh-copy-id.")
            sys.exit(1)

        if not test_ssh_connection(host, user, priv_key, port):
            print("[ERROR] Passwordless SSH test failed after key installation.")
            sys.exit(1)
        print("✓ Keyed SSH successfully configured!")

    # 3. Save Configuration
    config = {
        "host": host,
        "user": user,
        "port": port,
        "key_file": priv_key,
        "device_id": device_id
    }
    save_hil_config(config)
    print(f"✓ Connection settings saved to {CONFIG_FILE} and {ENV_FILE}")

    # 4. Remote System Package Provisioning
    print("\n[3/5] Installing dependencies on Raspberry Pi (apt packages, Mosquitto, gpiod)...")
    provision_script = """
set -e
echo '[Remote] Updating apt index...'
sudo apt update -qq

echo '[Remote] Installing system packages (git, python3-pip, virtualenv, gpiod, mosquitto)...'
sudo apt install -y -qq git python3-pip virtualenv python3-gpiod gpiod mosquitto mosquitto-clients

echo '[Remote] Configuring Mosquitto for local WLAN access...'
sudo bash -c 'cat <<EOF > /etc/mosquitto/conf.d/lan.conf
listener 1883
allow_anonymous true
EOF'
sudo systemctl enable -q mosquitto
sudo systemctl restart mosquitto

echo '[Remote] Configuring hardware UART on /dev/serial0...'
sudo raspi-config nonint do_serial_cons 1 || true
sudo raspi-config nonint do_serial_hw 0 || true

echo '[Remote] Creating test repository directory...'
mkdir -p ~/mqtt_micropy_slave
cd ~/mqtt_micropy_slave
if [ ! -d ".venv" ]; then
    echo '[Remote] Creating Python virtual environment with --system-site-packages...'
    virtualenv --system-site-packages .venv
fi
echo '[Remote] System provisioning complete.'
"""
    res = run_remote_command(host, user, priv_key, provision_script, port=port, check=False)
    if res.returncode != 0:
        print("[ERROR] Remote provisioning encountered an error.")
        sys.exit(res.returncode)
    print("✓ Remote system packages and services installed.")

    # 5. Sync Project Files
    print("\n[4/5] Syncing project files to Raspberry Pi...")
    cmd_sync(argparse.Namespace(host=host, user=user, port=port, key=priv_key))

    # 6. Install Python Requirements inside venv
    print("\n[5/5] Installing Python requirements in Pi virtual environment...")
    pip_install_cmd = "cd ~/mqtt_micropy_slave && source .venv/bin/activate && pip install -q -r requirements.txt"
    run_remote_command(host, user, priv_key, pip_install_cmd, port=port, check=True)
    print("✓ Virtual environment dependencies installed.")

    print("\n==================================================")
    print("           HIL RASPBERRY PI READY!               ")
    print("==================================================")
    print("You can now run HIL tests remotely at any time with:")
    print("  python tools/hil_manager.py run")
    print("or:")
    print("  pytest -v tests/hil/test_pi_hil.py")
    print("\nNote: If hardware UART was just enabled, a one-time reboot of the Pi may be required:")
    print("  python tools/hil_manager.py reboot")


def cmd_sync(args):
    """Sync local project code to remote Raspberry Pi using rsync or scp."""
    cfg = load_hil_config()
    host = getattr(args, "host", None) or cfg.get("host")
    user = getattr(args, "user", None) or cfg.get("user", DEFAULT_USER)
    port = getattr(args, "port", None) or cfg.get("port", DEFAULT_PORT)
    key_path = getattr(args, "key", None) or cfg.get("key_file") or get_default_key_path()

    if not host:
        print("[ERROR] No Pi host configured. Run 'python tools/hil_manager.py setup --host <IP>' first.")
        sys.exit(1)

    key_path = os.path.expanduser(key_path)
    remote_dest = f"{user}@{host}:~/mqtt_micropy_slave/"
    print(f"Syncing local files to {remote_dest}...")

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    if shutil.which("rsync"):
        rsync_cmd = [
            "rsync", "-avz", "--delete",
            "-e", f"ssh -i {key_path} -p {port} -o StrictHostKeyChecking=accept-new",
            "--exclude", ".venv",
            "--exclude", ".git",
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
            "--exclude", ".pytest_cache",
            f"{repo_root}/",
            remote_dest
        ]
        res = subprocess.run(rsync_cmd)
        if res.returncode == 0:
            print("✓ Rsync synchronization successful.")
            return
        print("[Warn] rsync failed, falling back to scp...")

    # Fallback to scp
    scp_dirs = ["src", "tests", "tools"]
    for d in scp_dirs:
        local_dir = os.path.join(repo_root, d)
        if os.path.exists(local_dir):
            subprocess.run(["scp", "-i", key_path, "-P", str(port), "-r", local_dir, remote_dest])
    for f in ["requirements.txt", "pyproject.toml", "config.example.json"]:
        local_f = os.path.join(repo_root, f)
        if os.path.exists(local_f):
            subprocess.run(["scp", "-i", key_path, "-P", str(port), local_f, remote_dest])

    print("✓ SCP synchronization complete.")


def cmd_run(args):
    """Sync latest files and execute HIL test suite on the remote Pi."""
    cfg = load_hil_config()
    host = getattr(args, "host", None) or cfg.get("host")
    user = getattr(args, "user", None) or cfg.get("user", DEFAULT_USER)
    port = getattr(args, "port", None) or cfg.get("port", DEFAULT_PORT)
    key_path = getattr(args, "key", None) or cfg.get("key_file") or get_default_key_path()
    device_id = getattr(args, "device_id", None) or cfg.get("device_id", DEFAULT_DEVICE_ID)

    if not host:
        print("[ERROR] No Pi host configured. Run 'python tools/hil_manager.py setup --host <IP>' first.")
        sys.exit(1)

    # Check connectivity
    if not is_port_open(host, port, timeout=2.5):
        print(f"[ERROR] Raspberry Pi {host}:{port} is offline or unreachable.")
        sys.exit(1)

    # Sync code unless --no-sync specified
    if not getattr(args, "no_sync", False):
        cmd_sync(args)

    print(f"\n[HIL Runner] Executing pytest on {user}@{host} (DUT: {device_id})...\n")

    test_filter = f"-k {args.filter}" if getattr(args, "filter", None) else ""
    remote_test_cmd = f"""
cd ~/mqtt_micropy_slave
source .venv/bin/activate
export MQTT_BROKER="127.0.0.1"
export DEVICE_ID="{device_id}"
pytest -v {test_filter} tests/hil/test_pi_hil.py
"""
    res = run_remote_command(host, user, key_path, remote_test_cmd, port=port, check=False)
    return res.returncode


def cmd_status(args):
    """Check connectivity, configuration, and remote services."""
    cfg = load_hil_config()
    host = getattr(args, "host", None) or cfg.get("host")
    user = getattr(args, "user", None) or cfg.get("user", DEFAULT_USER)
    port = getattr(args, "port", None) or cfg.get("port", DEFAULT_PORT)
    key_path = getattr(args, "key", None) or cfg.get("key_file") or get_default_key_path()

    print("==================================================")
    print("          Raspberry Pi HIL Rig Status             ")
    print("==================================================")
    if not host:
        print("Status: NOT CONFIGURED")
        print("Run 'python tools/hil_manager.py setup --host <IP>' to configure.")
        return

    print(f"Target:      {user}@{host}:{port}")
    print(f"Key File:    {key_path}")

    online = is_port_open(host, port, timeout=2.0)
    print(f"Network:     {'ONLINE (Port 22 reachable)' if online else 'OFFLINE'}")

    if online and os.path.exists(os.path.expanduser(key_path)):
        ssh_ok = test_ssh_connection(host, user, key_path, port)
        print(f"Keyed SSH:   {'AUTHENTICATED' if ssh_ok else 'AUTH FAILED'}")

        if ssh_ok:
            info_cmd = "systemctl is-active mosquitto && test -e /dev/serial0 && echo 'UART: PRESENT' || echo 'UART: NOT FOUND'"
            res = subprocess.run(
                ["ssh", "-i", os.path.expanduser(key_path), "-p", str(port), f"{user}@{host}", info_cmd],
                capture_output=True, text=True
            )
            print(f"Services:    {res.stdout.strip()}")


def cmd_reboot(args):
    """Reboot the remote Raspberry Pi."""
    cfg = load_hil_config()
    host = getattr(args, "host", None) or cfg.get("host")
    user = getattr(args, "user", None) or cfg.get("user", DEFAULT_USER)
    port = getattr(args, "port", None) or cfg.get("port", DEFAULT_PORT)
    key_path = getattr(args, "key", None) or cfg.get("key_file") or get_default_key_path()

    if not host:
        print("[ERROR] No Pi host configured.")
        sys.exit(1)

    print(f"Rebooting {host}...")
    run_remote_command(host, user, key_path, "sudo reboot", port=port, check=False)
    print("✓ Reboot signal sent. The Pi will be back online in ~20-30 seconds.")


def main():
    parser = argparse.ArgumentParser(description="Raspberry Pi HIL Manager and Remote Runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # setup
    p_setup = subparsers.add_parser("setup", help="Initial setup: SSH key injection and full remote provisioning")
    p_setup.add_argument("--host", required=True, help="IP or hostname of Raspberry Pi (e.g. 192.168.1.50)")
    p_setup.add_argument("--user", default=DEFAULT_USER, help=f"SSH username (default: {DEFAULT_USER})")
    p_setup.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"SSH port (default: {DEFAULT_PORT})")
    p_setup.add_argument("--key", help="Path to private SSH key (default: ~/.ssh/id_ed25519 or id_rsa)")
    p_setup.add_argument("--device-id", default=DEFAULT_DEVICE_ID, help=f"DUT Device ID (default: {DEFAULT_DEVICE_ID})")
    p_setup.set_defaults(func=cmd_setup)

    # sync
    p_sync = subparsers.add_parser("sync", help="Synchronize local code to the Raspberry Pi")
    p_sync.add_argument("--host", help="Pi host override")
    p_sync.add_argument("--user", help="Pi user override")
    p_sync.add_argument("--port", type=int, help="SSH port override")
    p_sync.add_argument("--key", help="SSH key override")
    p_sync.set_defaults(func=cmd_sync)

    # run
    p_run = subparsers.add_parser("run", help="Sync latest code and run HIL tests remotely on the Pi")
    p_run.add_argument("--host", help="Pi host override")
    p_run.add_argument("--user", help="Pi user override")
    p_run.add_argument("--port", type=int, help="SSH port override")
    p_run.add_argument("--key", help="SSH key override")
    p_run.add_argument("--device-id", help="DUT Device ID override")
    p_run.add_argument("--no-sync", action="store_true", help="Skip rsync step before running tests")
    p_run.add_argument("-k", "--filter", help="Filter pytest tests by keyword expression")
    p_run.set_defaults(func=cmd_run)

    # status
    p_status = subparsers.add_parser("status", help="Check Raspberry Pi connectivity and services")
    p_status.add_argument("--host", help="Pi host override")
    p_status.add_argument("--user", help="Pi user override")
    p_status.add_argument("--port", type=int, help="SSH port override")
    p_status.add_argument("--key", help="SSH key override")
    p_status.set_defaults(func=cmd_status)

    # reboot
    p_reboot = subparsers.add_parser("reboot", help="Reboot remote Raspberry Pi")
    p_reboot.add_argument("--host", help="Pi host override")
    p_reboot.add_argument("--user", help="Pi user override")
    p_reboot.add_argument("--port", type=int, help="SSH port override")
    p_reboot.add_argument("--key", help="SSH key override")
    p_reboot.set_defaults(func=cmd_reboot)

    args = parser.parse_args()
    ret = args.func(args)
    if isinstance(ret, int) and ret != 0:
        sys.exit(ret)


if __name__ == "__main__":
    main()
