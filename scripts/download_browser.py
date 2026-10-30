"""Install the tested official Linux Camoufox build, checking its published digest."""

from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

VERSION = "158.0.1-beta.41"
ASSETS = {
    "x86_64": (
        1303577033,
        "3f2eb1d0451fa58e45e43e55f5e6181565069791ae60c0574f0ceb1f01341c33",
    ),
    "arm64": (
        1297503606,
        "60e25f6eb95e13d9ec655bc02913d51057e669cb31542e6ace9d2190ab5380af",
    ),
}


def asset_for_machine(machine: str) -> tuple[str, int, str]:
    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if architecture not in ASSETS:
        raise ValueError(f"No pinned Linux browser build for architecture {machine!r}")
    size, digest = ASSETS[architecture]
    return f"camoufox-{VERSION}-lin.{architecture}.zip", size, digest


def verify_archive(path: Path, expected_digest: str) -> None:
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected_digest:
        raise ValueError("Browser archive does not match the publisher's SHA-256 digest")
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = PurePosixPath(member.filename)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError("Browser archive contains an unsafe extraction path")


def install(target: Path) -> None:
    if platform.system() != "Linux":
        raise ValueError("This build helper installs Linux browsers only")
    name, size, digest = asset_for_machine(platform.machine())
    url = f"https://github.com/daijro/camoufox/releases/download/v{VERSION}/{name}"
    with tempfile.TemporaryDirectory(prefix="camoufox-download-") as temporary:
        path = Path(temporary) / name
        for attempt in range(8):
            offset = path.stat().st_size if path.exists() else 0
            if offset == size:
                break
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=90) as source:
                    resume = offset and source.status == 206
                    if resume and not source.headers.get("Content-Range", "").startswith(
                        f"bytes {offset}-"
                    ):
                        raise ValueError("Unexpected browser download range")
                    with path.open("ab" if resume else "wb") as destination:
                        while chunk := source.read(1024 * 1024):
                            destination.write(chunk)
                if path.stat().st_size == size:
                    break
            except OSError:
                if attempt == 7:
                    raise
            time.sleep(1)
        if not path.exists() or path.stat().st_size != size:
            raise ValueError("Browser archive download is incomplete")
        verify_archive(path, digest)
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(["unzip", "-q", "-o", str(path), "-d", str(target)], check=True)
        (target / "camoufox-bin").chmod(0o755)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("/opt/camoufox"))
    install(parser.parse_args().target)
