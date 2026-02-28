"""credentials_manager.py — Secure credential storage via the OS keychain.

Uses the `keyring` library, which maps to:
  - Windows: Windows Credential Manager (no extra packages needed on keyring 25+)
  - macOS:   Keychain
  - Linux:   SecretService / kwallet

This module is pure data — no printing, no prompting. It is the only
place in the codebase that knows the keyring service name and key names.
"""

import os

import keyring
from keyring.errors import PasswordDeleteError

# The app identifier shown in the OS credential store (e.g., Windows Credential Manager)
SERVICE_NAME = "garmin-health-coach"

# All credential keys, in the order we want to prompt for them
CREDENTIAL_KEYS = ("garmin_email", "garmin_password", "anthropic_api_key")

# Labels used in UI output
CREDENTIAL_LABELS = {
    "garmin_email": "Garmin Email",
    "garmin_password": "Garmin Password",
    "anthropic_api_key": "Anthropic API Key",
}

# Fallback: map our internal keys to .env / environment variable names
_ENV_VAR_MAP = {
    "garmin_email": "GARMIN_EMAIL",
    "garmin_password": "GARMIN_PASSWORD",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}


def save_credential(key: str, value: str) -> None:
    """Persist a single credential to the OS keychain."""
    keyring.set_password(SERVICE_NAME, key, value)


def load_credential(key: str) -> str | None:
    """
    Load a credential, checking the OS keychain first, then environment variables.

    The env var fallback means:
      - Existing .env users keep working without any changes.
      - CI/headless environments can pass credentials via env vars.
    """
    try:
        value = keyring.get_password(SERVICE_NAME, key)
        if value:
            return value
    except Exception:
        # If keyring is misconfigured or unavailable, fall through to env vars
        pass

    return os.getenv(_ENV_VAR_MAP.get(key, "")) or None


def load_all_credentials() -> dict[str, str | None]:
    """Return all credentials as a dict; values are None if not found."""
    return {key: load_credential(key) for key in CREDENTIAL_KEYS}


def save_all_credentials(credentials: dict[str, str]) -> None:
    """Save a dict of {key: value} pairs to the OS keychain."""
    for key, value in credentials.items():
        if value:
            save_credential(key, value)


def credentials_complete() -> bool:
    """Return True only if every required credential is present and non-empty."""
    return all(load_credential(key) for key in CREDENTIAL_KEYS)


def delete_credential(key: str) -> None:
    """Remove a single credential from the keychain. Silently ignores if missing."""
    try:
        keyring.delete_password(SERVICE_NAME, key)
    except PasswordDeleteError:
        pass


def delete_all_credentials() -> None:
    """Remove all app credentials from the OS keychain."""
    for key in CREDENTIAL_KEYS:
        delete_credential(key)


def inject_into_env() -> None:
    """
    Write all stored credentials into os.environ.

    This lets the Anthropic SDK (which reads ANTHROPIC_API_KEY directly from
    os.environ) and any other os.getenv() calls pick up keychain-stored values
    without needing to pass credentials around explicitly.
    """
    for key in CREDENTIAL_KEYS:
        value = load_credential(key)
        env_var = _ENV_VAR_MAP[key]
        if value and not os.environ.get(env_var):
            # Only set if not already present — respects manually-set env vars
            os.environ[env_var] = value
