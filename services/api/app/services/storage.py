"""Media storage: Cloudflare R2 (S3-compatible) in production, local filesystem for dev/tests."""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from app.config import get_settings


class Storage(ABC):
    @abstractmethod
    def put_file(self, key: str, path: Path, content_type: str) -> None: ...

    @abstractmethod
    def get_file(self, key: str, dest: Path) -> Path: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def presigned_url(self, key: str, expires_s: int = 3600) -> str: ...

    def public_url(self, key: str) -> str | None:
        """Stable public URL (custom R2 domain) — only used for TikTok PULL_FROM_URL."""
        return None

    def size(self, key: str) -> int | None:
        return None


class LocalStorage(Storage):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(path, tmp)
        tmp.replace(dest)  # atomic: readers never see partial objects

    def get_file(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), dest)
        return dest

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def presigned_url(self, key: str, expires_s: int = 3600) -> str:
        return f"/v1/media/{key}"

    def size(self, key: str) -> int | None:
        p = self._path(key)
        return p.stat().st_size if p.exists() else None

    def local_path(self, key: str) -> Path:
        return self._path(key)


class R2Storage(Storage):
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_settings()
        self.bucket = s.r2_bucket
        self.public_base = s.r2_public_base_url.rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=s.r2_access_key_id,
            aws_secret_access_key=s.r2_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})

    def get_file(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(dest))
        return dest

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presigned_url(self, key: str, expires_s: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s
        )

    def public_url(self, key: str) -> str | None:
        return f"{self.public_base}/{key}" if self.public_base else None

    def size(self, key: str) -> int | None:
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception:
            return None


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "r2":
        return R2Storage()
    return LocalStorage(s.local_storage_dir)
