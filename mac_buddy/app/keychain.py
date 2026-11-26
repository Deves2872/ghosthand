"""The Anthropic API key, kept in the macOS Keychain (not in a file).

Developers can still use ANTHROPIC_API_KEY or the project's .env; the app
checks the environment first.
"""
from __future__ import annotations

import os
import subprocess

SERVICE = "mac-buddy"
ACCOUNT = "anthropic-api-key"


def get_key() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def save_key(key: str) -> bool:
    """Store the key. It goes to `security` on stdin, so it never appears in the process list."""
    key = key.strip()
    if not key or any(c in key for c in "\"\n\r"):
        return False
    r = subprocess.run(["security", "-i"], input=f'add-generic-password -U -s {SERVICE} -a {ACCOUNT} -w "{key}"\n',
                       capture_output=True, text=True)
    return r.returncode == 0 and get_key() == key


def delete_key():
    subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT], capture_output=True)


def load_into_env() -> bool:
    k = get_key()
    if k and not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        os.environ["ANTHROPIC_API_KEY"] = k
    return bool(k)
