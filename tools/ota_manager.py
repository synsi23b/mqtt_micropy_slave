#!/usr/bin/env python3
"""
Firmware Release Packager and SCP Publisher for ESP32 OTA updates.
Packages local src/ code into versioned bundles with SHA256 manifests,
and securely uploads them to the remote Nginx host using SCP.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

RELEASES_DIR = "releases"
SRC_DIR = "src"


def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def cmd_package(args):
    version = args.version.lstrip("v")
    target_rel_dir = os.path.join(RELEASES_DIR, version)
    os.makedirs(target_rel_dir, exist_ok=True)

    print(f"[1/3] Packaging version {version} from {SRC_DIR}/...")
    files_manifest = {}

    # Copy files into releases/<version>/
    for root, dirs, files in os.walk(SRC_DIR):
        for file in files:
            if file.endswith(".py") or file == "version.json":
                full_src = os.path.join(root, file)
                rel_path = os.path.relpath(full_src, SRC_DIR).replace("\\", "/")
                target_dest = os.path.join(target_rel_dir, rel_path)
                os.makedirs(os.path.dirname(target_dest), exist_ok=True)

                if file == "version.json":
                    # Generate version.json with target version
                    with open(target_dest, "w") as f:
                        json.dump({"version": version, "release_date": datetime.now(timezone.utc).isoformat()}, f, indent=2)
                else:
                    shutil.copy2(full_src, target_dest)

                file_hash = sha256_file(target_dest)
                file_size = os.path.getsize(target_dest)
                files_manifest[rel_path] = {
                    "url": f"{version}/{rel_path}",
                    "sha256": file_hash,
                    "size_bytes": file_size
                }

    print(f"[2/3] Packaged {len(files_manifest)} files into {target_rel_dir}/")

    # Generate top-level manifest.json
    manifest_data = {
        "version": version,
        "release_date": datetime.now(timezone.utc).isoformat(),
        "files": files_manifest
    }

    manifest_path = os.path.join(RELEASES_DIR, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest_data, f, indent=2)

    print(f"[3/3] Created manifest at {manifest_path}")
    print(f"\n[SUCCESS] Release {version} is ready for publishing!")


def cmd_publish(args):
    manifest_path = os.path.join(RELEASES_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"[ERROR] No release manifest found at {manifest_path}. Run 'package' first!")
        sys.exit(1)

    host = args.host
    remote_path = args.path.rstrip("/")
    port = args.port

    print(f"Deploying releases directory to {host}:{remote_path} via SCP...")

    # Build SCP command
    # Windows/Linux scp: scp -P <port> -r releases/* user@host:/var/www/firmware/
    scp_cmd = ["scp"]
    if port:
        scp_cmd.extend(["-P", str(port)])
    scp_cmd.append("-r")

    # Include all release versions and manifest
    for item in os.listdir(RELEASES_DIR):
        local_item = os.path.join(RELEASES_DIR, item)
        scp_cmd.append(local_item)

    scp_cmd.append(f"{host}:{remote_path}/")

    print(f"Executing: {' '.join(scp_cmd)}")
    res = subprocess.run(scp_cmd)
    if res.returncode == 0:
        print("\n[SUCCESS] Firmware release successfully published to Nginx server!")
    else:
        print(f"\n[ERROR] SCP failed with exit code {res.returncode}")
        sys.exit(res.returncode)


def main():
    parser = argparse.ArgumentParser(description="ESP32 OTA Release Manager and SCP Publisher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # package
    p_pkg = subparsers.add_parser("package", help="Package current src into a versioned release")
    p_pkg.add_argument("--version", required=True, help="Semantic version tag (e.g. 1.1.0)")
    p_pkg.set_defaults(func=cmd_package)

    # publish
    p_pub = subparsers.add_parser("publish", help="Publish packaged releases to remote Nginx server via SCP")
    p_pub.add_argument("--host", required=True, help="SSH user and host (e.g. pi@192.168.1.50)")
    p_pub.add_argument("--path", required=True, help="Remote path where Nginx serves firmware (e.g. /var/www/firmware)")
    p_pub.add_argument("--port", type=int, default=22, help="SSH Port (default 22)")
    p_pub.set_defaults(func=cmd_publish)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
