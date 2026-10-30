import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "download_browser", Path(__file__).resolve().parents[1] / "scripts/download_browser.py"
)
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


@pytest.mark.parametrize("machine", ["x86_64", "amd64", "arm64", "aarch64"])
def test_browser_asset_is_pinned_for_supported_linux_architectures(machine):
    name, size, digest = download.asset_for_machine(machine)
    assert download.VERSION in name
    assert size > 0
    assert len(digest) == 64


def test_unsupported_browser_architecture_is_rejected():
    with pytest.raises(ValueError, match="architecture"):
        download.asset_for_machine("riscv64")


def archive_bytes(name="camoufox-bin"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, b"test browser")
    return stream.getvalue()


def test_verified_archive_is_accepted(tmp_path):
    payload = archive_bytes()
    path = tmp_path / "browser.zip"
    path.write_bytes(payload)

    download.verify_archive(path, hashlib.sha256(payload).hexdigest())


def test_browser_digest_mismatch_is_rejected_before_extracting(tmp_path):
    path = tmp_path / "browser.zip"
    path.write_bytes(archive_bytes())

    with pytest.raises(ValueError, match="SHA-256"):
        download.verify_archive(path, "0" * 64)


@pytest.mark.parametrize("name", ["../escape", "/absolute"])
def test_browser_archive_cannot_escape_target_directory(tmp_path, name):
    payload = archive_bytes(name)
    path = tmp_path / "browser.zip"
    path.write_bytes(payload)

    with pytest.raises(ValueError, match="unsafe extraction"):
        download.verify_archive(path, hashlib.sha256(payload).hexdigest())
