"""Custom exceptions for distributed KV store."""


class KVStoreError(Exception):
    """Base exception for all KV store errors."""


class NotLeaderError(KVStoreError):
    """Raised when a write is attempted on a non-leader node."""

    def __init__(self, leader_id: str | None = None) -> None:
        self.leader_id = leader_id
        msg = f"Not the leader. Current leader: {leader_id}" if leader_id else "Not the leader"
        super().__init__(msg)


class ConsensusError(KVStoreError):
    """Raised when consensus cannot be reached."""


class LogReplicationError(KVStoreError):
    """Raised when log replication fails."""


class ElectionError(KVStoreError):
    """Raised when an election fails."""


class SnapshotError(KVStoreError):
    """Raised when snapshot creation or loading fails."""
