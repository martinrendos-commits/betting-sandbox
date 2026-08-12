import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The suite runs against synthetic fixtures on the demo clock so that matches are
# always in play, regardless of when the tests happen to run.
os.environ.setdefault("MOCK_FIXTURES", "synthetic")
os.environ.setdefault("MOCK_CLOCK", "demo")
os.environ.setdefault("MOCK_MINUTES_PER_SECOND", "0.05")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("mock site did not start")


@pytest.fixture(scope="session")
def mock_site() -> str:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from mocksite.app import create_app; create_app().run(port={port})",
        ],
        cwd=ROOT,
        env={**os.environ},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(port)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=10)
