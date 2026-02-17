"""Data models for the Raft consensus KV store.

Defines node states, log entries, configuration, and cluster status
using Pydantic for validation and dataclasses for hot-path structures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_ELECTION_TIMEOUT_MIN_MS = 150
DEFAULT_ELECTION_TIMEOUT_MAX_MS = 300
DEFAULT_HEARTBEAT_INTERVAL_MS = 50
DEFAULT_CLUSTER_SIZE = 3
MAX_CLUSTER_SIZE = 9


class NodeState(str, Enum):
    """Raft node states."""

    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


class CommandType(str, Enum):
    """Types of state machine commands."""

    SET = "set"
    DELETE = "delete"
    BATCH_SET = "batch_set"
    BATCH_DELETE = "batch_delete"
    NOOP = "noop"


@dataclass
class LogEntry:
    """A single entry in the Raft log.

    Each entry contains a command to be applied to the state machine
    once committed by a majority of nodes.

    Attributes:
        term: The leader's term when this entry was created.
        index: Position in the log (1-indexed).
        command_type: Type of operation.
        key: Key for the operation (empty for batch/noop).
        value: Value for SET operations.
        batch_data: Key-value pairs for batch operations.
    """

    term: int = 0
    index: int = 0
    command_type: CommandType = CommandType.NOOP
    key: str = ""
    value: str = ""
    batch_data: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "term": self.term,
            "index": self.index,
            "command_type": self.command_type.value,
            "key": self.key,
            "value": self.value,
            "batch_data": self.batch_data,
        }


class RaftConfig(BaseModel):
    """Configuration for a Raft cluster.

    Attributes:
        cluster_size: Number of nodes in the cluster (must be odd).
        election_timeout_min_ms: Minimum election timeout in ms.
        election_timeout_max_ms: Maximum election timeout in ms.
        heartbeat_interval_ms: Heartbeat interval in ms.
        max_log_entries_per_append: Max entries per AppendEntries RPC.
        snapshot_threshold: Compact log after this many committed entries.
    """

    cluster_size: int = Field(default=DEFAULT_CLUSTER_SIZE, ge=1, le=MAX_CLUSTER_SIZE)
    election_timeout_min_ms: int = Field(default=DEFAULT_ELECTION_TIMEOUT_MIN_MS, ge=50)
    election_timeout_max_ms: int = Field(default=DEFAULT_ELECTION_TIMEOUT_MAX_MS, ge=100)
    heartbeat_interval_ms: int = Field(default=DEFAULT_HEARTBEAT_INTERVAL_MS, ge=10)
    max_log_entries_per_append: int = Field(default=100, ge=1)
    snapshot_threshold: int = Field(default=1000, ge=1)


@dataclass
class AppendEntriesRequest:
    """AppendEntries RPC request (log replication + heartbeat).

    Attributes:
        term: Leader's current term.
        leader_id: ID of the leader sending the request.
        prev_log_index: Index of log entry preceding new ones.
        prev_log_term: Term of prev_log_index entry.
        entries: Log entries to replicate (empty for heartbeat).
        leader_commit: Leader's commit index.
    """

    term: int = 0
    leader_id: str = ""
    prev_log_index: int = 0
    prev_log_term: int = 0
    entries: list[LogEntry] = field(default_factory=list)
    leader_commit: int = 0


@dataclass
class AppendEntriesResponse:
    """AppendEntries RPC response.

    Attributes:
        term: Responder's current term.
        success: True if entries were replicated.
        match_index: Highest log index known to be replicated.
    """

    term: int = 0
    success: bool = False
    match_index: int = 0


@dataclass
class RequestVoteRequest:
    """RequestVote RPC request (leader election).

    Attributes:
        term: Candidate's current term.
        candidate_id: ID of the candidate requesting votes.
        last_log_index: Index of candidate's last log entry.
        last_log_term: Term of candidate's last log entry.
    """

    term: int = 0
    candidate_id: str = ""
    last_log_index: int = 0
    last_log_term: int = 0


@dataclass
class RequestVoteResponse:
    """RequestVote RPC response.

    Attributes:
        term: Responder's current term.
        vote_granted: True if the vote was granted.
    """

    term: int = 0
    vote_granted: bool = False


@dataclass
class ClusterStatus:
    """Status of the Raft cluster.

    Attributes:
        leader_id: Current leader's node ID.
        term: Current term number.
        num_nodes: Total number of nodes.
        committed_index: Highest committed log index.
        total_entries: Total log entries across all nodes.
        state_machine_size: Number of keys in the state machine.
    """

    leader_id: str = ""
    term: int = 0
    num_nodes: int = 0
    committed_index: int = 0
    total_entries: int = 0
    state_machine_size: int = 0
