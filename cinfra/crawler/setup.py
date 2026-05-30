# Obscura download + serve manager
from __future__ import annotations

import stat
import subprocess
import tarfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen, urlretrieve

from cinfra.core.logging import get_logger

LOGGER = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

URL = "https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-x86_64-linux.tar.gz"

EXECUTABLES = ("obscura", "obscura-worker")


class Obscura:
    def __init__(self, install_dir: Path | None = None) -> None:
        self.install_dir = install_dir or (PROJECT_ROOT / ".obscura")
        self.archive_path = self.install_dir / "obscura-x86_64-linux.tar.gz"

    @property
    def binary(self) -> Path:
        return self.install_dir / "obscura"

    def is_installed(self) -> bool:
        return all((self.install_dir / name).is_file() for name in EXECUTABLES)

    def install(self, force: bool = False) -> Path:
        if self.is_installed() and not force:
            LOGGER.info("Obscura already installed at %s", self.install_dir)
            return self.install_dir

        self.install_dir.mkdir(parents=True, exist_ok=True)

        LOGGER.info("Downloading Obscura archive to %s", self.archive_path)
        urlretrieve(URL, self.archive_path)

        LOGGER.info("Extracting Obscura archive into %s", self.install_dir)
        with tarfile.open(self.archive_path, "r:gz") as tar:
            tar.extractall(path=self.install_dir, filter="data")

        # Drop the tarball now that it's been unpacked.
        self.archive_path.unlink()
        LOGGER.info("Removed Obscura archive %s", self.archive_path)

        self._mark_executable()
        self._verify()

        LOGGER.info("Obscura installed at %s", self.install_dir)
        return self.install_dir

    def _mark_executable(self) -> None:
        LOGGER.info("Marking extracted files as executable")
        for path in self.install_dir.rglob("*"):
            if path.is_file() and path != self.archive_path:
                mode = path.stat().st_mode
                path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _verify(self) -> None:
        missing = [
            name for name in EXECUTABLES if not (self.install_dir / name).is_file()
        ]
        if missing:
            raise RuntimeError(
                f"Obscura install incomplete, missing executables: {', '.join(missing)}"
            )
        LOGGER.info("Verified executables present: %s", ", ".join(EXECUTABLES))

    @staticmethod
    def _is_running(port: int, host: str = "127.0.0.1") -> bool:
        # Hit the CDP HTTP endpoint rather than a raw socket: a bare connect +
        # close makes the CDP server log "Handshake not finished".
        try:
            with urlopen(f"http://{host}:{port}/json/version", timeout=0.5):
                return True
        except (URLError, OSError):
            return False

    def serve(
        self, port: int = 9222, stealth: bool = True
    ) -> subprocess.Popen[bytes] | None:
        # If Obscura is already up, reuse it instead of crashing on a bind error.
        if self._is_running(port):
            LOGGER.info(
                "Obscura already running on port %d; reusing existing server", port
            )
            return None

        if not self.is_installed():
            self.install()

        cmd = [str(self.binary), "serve", "--port", str(port)]
        if stealth:
            cmd.append("--stealth")

        LOGGER.info("Starting Obscura server: %s", " ".join(cmd))
        return subprocess.Popen(cmd)


if __name__ == "__main__":
    proc = Obscura().serve()
    if proc is not None:
        proc.wait()
