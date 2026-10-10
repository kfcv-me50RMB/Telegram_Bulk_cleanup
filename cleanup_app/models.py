from dataclasses import dataclass


@dataclass(frozen=True)
class CleanupSelection:
    account_id: str
    groups: tuple
    private_users: tuple
    contacts: tuple


class CleanupStopped(RuntimeError):
    def __init__(self, reason, unknown=False):
        super().__init__(reason)
        self.unknown = unknown


class StoredSessionInvalidError(RuntimeError):
    """The registry entry does not contain a reusable authorized session."""


class QRLoginCancelled(RuntimeError):
    """User cancelled QR login; do not present it as a network failure."""

