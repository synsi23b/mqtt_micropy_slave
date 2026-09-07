"""Unit tests for OTA client, semantic versioning, and rollback safety."""
import pytest
import os
import json
from net.ota import OTAClient, is_newer_version, parse_semver


def test_semver():
    assert parse_semver("1.2.3") == (1, 2, 3)
    assert parse_semver("v2.0.1") == (2, 0, 1)

    assert is_newer_version("1.1.0", "1.0.0") is True
    assert is_newer_version("1.0.1", "1.0.0") is True
    assert is_newer_version("2.0.0", "1.9.9") is True
    assert is_newer_version("1.0.0", "1.0.0") is False
    assert is_newer_version("0.9.0", "1.0.0") is False


@pytest.mark.asyncio
async def test_ota_check_update(tmp_path):
    ver_file = str(tmp_path / "version.json")
    with open(ver_file, "w") as f:
        json.dump({"version": "1.0.0"}, f)

    ota = OTAClient(manifest_url="http://mock-server/manifest.json", version_file=ver_file)

    # Mock fetch_http
    manifest_json = json.dumps({
        "version": "1.1.0",
        "files": {
            "main.py": {"url": "1.1.0/main.py"}
        }
    })
    async def mock_fetch(url):
        return manifest_json

    ota.fetch_http = mock_fetch

    has_upd, remote_ver, manifest, err = await ota.check_update()
    assert has_upd is True
    assert remote_ver == "1.1.0"
    assert err is None


@pytest.mark.asyncio
async def test_ota_apply_success(tmp_path):
    ver_file = str(tmp_path / "version.json")
    with open(ver_file, "w") as f:
        json.dump({"version": "1.0.0"}, f)

    ota = OTAClient(manifest_url="http://mock-server/manifest.json", version_file=ver_file)

    target_file = str(tmp_path / "test_target.py")
    with open(target_file, "w") as f:
        f.write("# Old code")

    manifest_json = json.dumps({
        "version": "1.1.0",
        "files": {
            target_file: {"url": "test_target.py"}
        }
    })

    async def mock_fetch(url):
        if "manifest.json" in url:
            return manifest_json
        return "# New updated code v1.1.0"

    ota.fetch_http = mock_fetch

    ok, res = await ota.apply_update(auto_reboot=False)
    assert ok is True
    assert res == "1.1.0"

    # Verify file content was replaced
    with open(target_file, "r") as f:
        assert f.read() == "# New updated code v1.1.0"

    # Verify version.json was updated
    with open(ver_file, "r") as f:
        data = json.load(f)
        assert data["version"] == "1.1.0"


@pytest.mark.asyncio
async def test_ota_rollback_on_download_error(tmp_path):
    ver_file = str(tmp_path / "version.json")
    with open(ver_file, "w") as f:
        json.dump({"version": "1.0.0"}, f)

    ota = OTAClient(manifest_url="http://mock-server/manifest.json", version_file=ver_file)

    file_a = str(tmp_path / "file_a.py")
    file_b = str(tmp_path / "file_b.py")
    with open(file_a, "w") as f:
        f.write("# Original A")

    manifest_json = json.dumps({
        "version": "1.1.0",
        "files": {
            file_a: {"url": "file_a.py"},
            file_b: {"url": "file_b.py"}
        }
    })

    async def mock_fetch(url):
        if "manifest.json" in url:
            return manifest_json
        if "file_a.py" in url:
            return "# New A"
        # Simulate network failure on file B
        raise RuntimeError("Connection dropped during file B download")

    ota.fetch_http = mock_fetch

    ok, err = await ota.apply_update(auto_reboot=False)
    assert ok is False
    assert "Connection dropped" in err

    # Ensure file_a was NOT modified (rollback)
    with open(file_a, "r") as f:
        assert f.read() == "# Original A"

    # Ensure staging .new files were cleaned up
    assert not os.path.exists(file_a + ".new")
    assert not os.path.exists(file_b + ".new")
