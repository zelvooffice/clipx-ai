"""Test harness: real PostgreSQL (schema from the Alembic migration), real Redis, local storage.

External HTTP is never reached in unit/contract tests (respx). Tests marked ``integration`` use real
credentials from the environment and are skipped when absent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://clipx:clipx@127.0.0.1:5432/clipx_test")
_REAL = {k: os.environ.get(k, "") for k in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "TIKTOK_CLIENT_KEY",
                                             "TIKTOK_CLIENT_SECRET", "ANTHROPIC_API_KEY")}
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": TEST_DB,
    "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
    "TOKEN_ENCRYPTION_KEYS": base64.b64encode(b"k" * 32).decode(),
    "INTERNAL_API_SECRET": "test-internal-secret-0123456789abcdef0123456789",
    "AUTHORIZED_EMAILS": "owner@example.com",
    "TWITCH_CLIENT_ID": "test-client-id",
    "TWITCH_CLIENT_SECRET": "test-client-secret",
    "TWITCH_TARGET_LOGIN": "lacy",
    "TWITCH_EVENTSUB_SECRET": "eventsub-secret-for-tests",
    "TIKTOK_CLIENT_KEY": "test-client-key",
    "TIKTOK_CLIENT_SECRET": "test-tiktok-secret",
    "PUBLIC_APP_URL": "https://clipx.example.com",
    "STORAGE_BACKEND": "local",
    "ANTHROPIC_API_KEY": "",
    "LIVE_INGEST_URL": "",
    "WHISPER_MODEL": os.environ.get("WHISPER_MODEL", "tiny.en"),
    "RATE_LIMIT_PER_MINUTE": "1000",
})

REAL_CREDENTIALS = _REAL


@pytest.fixture(scope="session", autouse=True)
def _database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    os.environ["LOCAL_STORAGE_DIR"] = str(tmp_path_factory.mktemp("storage"))
    os.environ["WORK_DIR"] = str(tmp_path_factory.mktemp("work"))
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from alembic import command

    eng = create_engine(TEST_DB)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    eng.dispose()
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(cfg, "head")  # the migration itself is under test
    yield


@pytest.fixture(autouse=True)
def _clean(_database: None) -> Iterator[None]:
    from app.config import get_settings
    from app.db import Base, get_engine
    from app.services.redis_client import get_redis
    from app.services.storage import get_storage

    get_settings.cache_clear()
    get_storage.cache_clear()
    get_redis.cache_clear()
    get_redis().flushdb()
    with get_engine().begin() as c:
        names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        c.exec_driver_sql(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    yield


class JobDrain:
    """Deterministic in-process job execution (replaces Celery in tests)."""

    def __init__(self) -> None:
        self.queue: list[tuple[uuid.UUID, float]] = []

    def __call__(self, job_id: uuid.UUID, countdown: float) -> None:
        self.queue.append((job_id, countdown))

    def run(self, *, max_steps: int = 200, ignore_delay: bool = True, kinds: set[str] | None = None) -> list[str]:
        from app.db import session_scope
        from app.models import ProcessingJob
        from app.services.jobs import run_job

        results: list[str] = []
        steps = 0
        while self.queue and steps < max_steps:
            job_id, _ = self.queue.pop(0)
            with session_scope() as db:
                job = db.get(ProcessingJob, job_id)
                if job is None:
                    continue
                if kinds and job.kind not in kinds:
                    continue
                if ignore_delay:
                    job.run_after = None
            results.append(f"{job.kind}:{run_job(job_id)}")
            steps += 1
        return results


@pytest.fixture
def drain() -> Iterator[JobDrain]:
    from app.services import jobs

    d = JobDrain()
    jobs.set_dispatcher(d)
    yield d
    jobs.set_dispatcher(None)


@pytest.fixture(autouse=True)
def _default_dispatcher() -> Iterator[None]:
    """Never touch a real broker in tests: collect dispatches unless a test asks for `drain`."""
    from app.services import jobs

    jobs.set_dispatcher(lambda job_id, countdown: None)
    yield
    jobs.set_dispatcher(None)


def signed_headers(method: str, path: str, body: bytes = b"", email: str = "owner@example.com",
                   ts: int | None = None, nonce: str | None = None, secret: str | None = None) -> dict[str, str]:
    from app.security import signing

    ts_s = str(ts if ts is not None else int(time.time()))
    nonce = nonce or secrets.token_hex(16)
    sig = signing.sign(secret or os.environ["INTERNAL_API_SECRET"], timestamp=ts_s, nonce=nonce, method=method,
                       path=path, email=email, body=body)
    return {"x-clipx-email": email, "x-clipx-timestamp": ts_s, "x-clipx-nonce": nonce, "x-clipx-signature": sig}


class SignedClient:
    def __init__(self, client, email: str = "owner@example.com") -> None:  # type: ignore[no-untyped-def]
        self.c = client
        self.email = email

    def request(self, method: str, path: str, json_body: object | None = None):  # type: ignore[no-untyped-def]
        body = json.dumps(json_body).encode() if json_body is not None else b""
        headers = signed_headers(method, path, body, self.email)
        if json_body is not None:
            headers["content-type"] = "application/json"
        return self.c.request(method, path, content=body, headers=headers)

    def get(self, path: str):  # type: ignore[no-untyped-def]
        return self.request("GET", path)

    def post(self, path: str, json_body: object | None = None):  # type: ignore[no-untyped-def]
        return self.request("POST", path, json_body if json_body is not None else {})

    def put(self, path: str, json_body: object):  # type: ignore[no-untyped-def]
        return self.request("PUT", path, json_body)

    def patch(self, path: str, json_body: object):  # type: ignore[no-untyped-def]
        return self.request("PATCH", path, json_body)

    def delete(self, path: str):  # type: ignore[no-untyped-def]
        return self.request("DELETE", path)


@pytest.fixture
def client() -> Iterator[SignedClient]:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield SignedClient(c)


@pytest.fixture
def raw_client():  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


# ------------------------------------------------------------------------------- media fixtures
@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


def make_video(path: Path, seconds: float = 8, size: str = "1920x1080", tone: bool = True) -> Path:
    import subprocess

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if tone:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    # 2 s keyframe interval, like real Twitch/OBS streams (Twitch recommends 2 s)
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "60", "-keyint_min", "60"]
    if tone:
        cmd += ["-c:a", "aac"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def speech_video(media_dir: Path) -> Path:
    """A real video with real synthesized speech (espeak-ng) — used to test ASR and subtitle sync."""
    import shutil
    import subprocess

    if not shutil.which("espeak-ng"):
        pytest.skip("espeak-ng not installed")
    wav = media_dir / "speech.wav"
    text = "Hello chat. This is a test of the clipping system. The subtitles should follow every word."
    subprocess.run(["espeak-ng", "-s", "150", "-w", str(wav), text], check=True)
    out = media_dir / "speech.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=1280x720:rate=30", "-i", str(wav), "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    return out


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()
