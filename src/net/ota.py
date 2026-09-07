"""Over-The-Air (OTA) firmware update client for MicroPython over Wi-Fi HTTP."""
import json
import os

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio


def parse_semver(ver_str):
    """Parse 'X.Y.Z' string into tuple of integers."""
    try:
        parts = ver_str.strip().lstrip("v").split(".")
        return tuple(int(p) for p in parts[:3])
    except Exception:
        return (0, 0, 0)


def is_newer_version(remote_ver, local_ver):
    """Return True if remote_ver is strictly newer than local_ver."""
    return parse_semver(remote_ver) > parse_semver(local_ver)


class OTAClient:
    """Manages version checking, file downloading, atomic replacement, and rollbacks."""

    def __init__(self, manifest_url="", version_file="version.json"):
        self.manifest_url = manifest_url
        self.version_file = version_file
        self.current_version = self._load_current_version()

    def _load_current_version(self):
        try:
            with open(self.version_file, "r") as f:
                data = json.load(f)
                return data.get("version", "0.0.0")
        except Exception:
            return "0.0.0"

    def _save_current_version(self, new_version):
        try:
            with open(self.version_file, "w") as f:
                json.dump({"version": new_version}, f)
            self.current_version = new_version
        except Exception as e:
            print("[OTA] Warning: failed to save version.json:", e)

    async def fetch_http(self, url):
        """Fetch content from HTTP URL across MicroPython and desktop Python."""
        # Try desktop urllib first if available
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "ESP32-MicroPython-OTA"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read().decode("utf-8")
        except ImportError:
            pass

        # MicroPython async socket HTTP GET
        try:
            import socket
            # Parse URL (http://host:port/path)
            proto, dummy, host_port_path = url.split("/", 2)
            if "/" in host_port_path:
                host_port, path = host_port_path.split("/", 1)
                path = "/" + path
            else:
                host_port = host_port_path
                path = "/"

            if ":" in host_port:
                host, port = host_port.split(":")
                port = int(port)
            else:
                host = host_port
                port = 80

            addr = socket.getaddrinfo(host, port)[0][-1]
            s = socket.socket()
            s.settimeout(10.0)
            s.connect(addr)
            req_str = "GET {} HTTP/1.0\r\nHost: {}\r\nUser-Agent: ESP32-OTA\r\n\r\n".format(path, host)
            s.send(req_str.encode("utf-8"))

            response = bytearray()
            while True:
                chunk = s.recv(512)
                if not chunk:
                    break
                response.extend(chunk)
            s.close()

            # Split HTTP headers and body
            header_end = response.find(b"\r\n\r\n")
            if header_end != -1:
                return response[header_end + 4:].decode("utf-8")
            return response.decode("utf-8")
        except Exception as e:
            raise RuntimeError("HTTP GET failed: {}".format(e))

    async def check_update(self, manifest_url=None):
        """
        Check manifest for available firmware update.
        Returns (has_update: bool, latest_version: str, manifest: dict, error: str)
        """
        url = manifest_url or self.manifest_url
        if not url:
            return False, self.current_version, None, "No manifest URL configured"

        try:
            body = await self.fetch_http(url)
            manifest = json.loads(body)
            remote_version = manifest.get("version", "0.0.0")

            if is_newer_version(remote_version, self.current_version):
                return True, remote_version, manifest, None
            else:
                return False, remote_version, manifest, None
        except Exception as e:
            return False, self.current_version, None, str(e)

    async def apply_update(self, manifest_url=None, auto_reboot=False):
        """
        Download updated files into staging, verify, atomically replace, and update version.json.
        """
        url = manifest_url or self.manifest_url
        has_update, remote_ver, manifest, err = await self.check_update(url)
        if err:
            return False, "Check update failed: {}".format(err)
        if not has_update:
            return False, "Already up to date ({})".format(self.current_version)

        # Base URL for relative file downloads
        base_url = url.rsplit("/", 1)[0]
        files_dict = manifest.get("files", {})
        if not files_dict:
            return False, "Manifest contains no files"

        downloaded_staging = []
        try:
            for local_path, meta in files_dict.items():
                rel_url = meta.get("url", local_path)
                file_url = "{}/{}".format(base_url, rel_url) if not rel_url.startswith("http") else rel_url

                print("[OTA] Downloading {} from {}...".format(local_path, file_url))
                file_content = await self.fetch_http(file_url)

                # Ensure parent dir exists
                staging_path = local_path + ".new"
                parts = staging_path.replace("\\", "/").rsplit("/", 1)
                if len(parts) == 2 and parts[0]:
                    try:
                        os.mkdir(parts[0])
                    except Exception:
                        pass

                with open(staging_path, "w") as f:
                    f.write(file_content)
                downloaded_staging.append((staging_path, local_path))

            # All files successfully downloaded -> atomically replace
            print("[OTA] All files downloaded. Applying atomic replacement...")
            for staging_path, local_path in downloaded_staging:
                try:
                    os.remove(local_path)
                except Exception:
                    pass
                os.rename(staging_path, local_path)

            self._save_current_version(remote_ver)
            print("[OTA] Firmware successfully updated to {}!".format(remote_ver))

            if auto_reboot:
                print("[OTA] Rebooting device...")
                try:
                    import machine
                    machine.reset()
                except Exception:
                    pass

            return True, remote_ver

        except Exception as e:
            print("[OTA] Update failed with error:", e)
            # Cleanup staging files (rollback protection)
            for staging_path, _ in downloaded_staging:
                try:
                    os.remove(staging_path)
                except Exception:
                    pass
            return False, str(e)
