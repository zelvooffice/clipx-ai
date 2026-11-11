"""Dashboard password hashing (scrypt). Only the hash is ever stored.

Generate a hash:  python -m app.security.password
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import sys

N, R, P = 2**15, 8, 1  # ~32 MB, a few tens of ms per check


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=64 * 1024 * 1024, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    # ":" separators: safe in .env files, shells and docker compose (no "$" interpolation)
    return f"scrypt:{N}:{R}:{P}:{b64(salt)}:{b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = encoded.replace("$", ":").split(":")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
                            maxmem=64 * 1024 * 1024, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def main() -> None:
    pw = getpass.getpass("New dashboard password (min 12 chars): ")
    if pw != getpass.getpass("Repeat: "):
        sys.exit("passwords do not match")
    print(hash_password(pw))


if __name__ == "__main__":
    main()
