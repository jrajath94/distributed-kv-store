"""Core Raft consensus implementation with KV state machine.

Four components with clear separation:
  1. StateMachine -- the key-value store (applied deterministically)
  2. RaftLog -- append-only log with term tracking
  3. RaftNode -- single Raft node with election/replication logic
  4. RaftCluster -- in-process cluster for testing and demonstration

The Raft algorithm guarantees linearizable writes through leader-based
log replication and majority commitment.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from distributed_kv_store.exceptions import (
    ConsensusError,
    ElectionError,
    NotLeaderError,
)
from distributed_kv_store.models import (
    AppendEntriesRequest,
    AppendEntriesResponse,
    ClusterStatus,
    CommandType,
    LogEntry,
    NodeState,
    RaftConfig,
    RequestVoteRequest,
    RequestVoteResponse,
)
from distributed_kv_store.utils import majority_count, monotonic_ms

logger = logging.getLogger(__name__)


class StateMachine:
    """Deterministic key-value state machine.

    Applied identically on all nodes. The state machine is the
    "replicated" component -- Raft ensures all nodes apply the
    same sequence of commands.

    Supports single and batch operations for ML metadata patterns
    (e.g., storing hyperparameters, metrics, checkpoints).
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._version: int = 0

    @property
    def size(self) -> int:
        """Number of keys in the store."""
        return len(self._store)

    @property
    def version(self) -> int:
        """Current state machine version (number of applied commands)."""
        return self._version

    def apply(self, entry: LogEntry) -> Optional[str]:
        """Apply a log entry to the state machine.

        Args:
            entry: The committed log entry to apply.

        Returns:
            Value for GET operations, None otherwise.
        """
        self._version += 1

        if entry.command_type == CommandType.SET:
            self._store[entry.key] = entry.value
            return None
        elif entry.command_type == CommandType.DELETE:
            self._store.pop(entry.key, None)
            return None
        elif entry.command_type == CommandType.BATCH_SET:
            self._store.update(entry.batch_data)
            return None
        elif entry.command_type == CommandType.BATCH_DELETE:
            for key in entry.batch_data:
                self._store.pop(key, None)
            return None
        elif entry.command_type == CommandType.NOOP:
            return None
        return None

    def get(self, key: str) -> Optional[str]:
        """Read a value from the state machine.

        Reads do not go through Raft -- they are served directly
        from the local state machine. For linearizable reads,
        the leader must verify its leadership first.

        Args:
            key: Key to look up.

        Returns:
            Value if found, None otherwise.
        """
        return self._store.get(key)

    def get_all(self) -> dict[str, str]:
        """Get a snapshot of all key-value pairs.

        Returns:
            Copy of the entire store.
        """
        return dict(self._store)

    def keys(self) -> list[str]:
        """Get all keys in the store.

        Returns:
            List of keys.
        """
        return list(self._store.keys())

    def snapshot(self) -> dict[str, Any]:
        """Create a serializable snapshot of the state machine.

        Returns:
            Dictionary with store contents and version.
        """
        return {
            "store": dict(self._store),
            "version": self._version,
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore state machine from a snapshot.

        Args:
            snapshot: Dictionary from a previous snapshot() call.
        """
        self._store = dict(snapshot["store"])
        self._version = snapshot["version"]


class RaftLog:
    """Append-only log with term tracking.

    Entries are 1-indexed (index 0 is a sentinel). Each entry
    contains the leader's term and a command for the state machine.
    """

    def __init__(self) -> None:
        # Sentinel entry at index 0
        self._entries: list[LogEntry] = [
            LogEntry(term=0, index=0, command_type=CommandType.NOOP)
        ]

    @property
    def last_index(self) -> int:
        """Index of the last log entry."""
        return len(self._entries) - 1

    @property
    def last_term(self) -> int:
        """Term of the last log entry."""
        return self._entries[-1].term

    def get(self, index: int) -> Optional[LogEntry]:
        """Get a log entry by index.

        Args:
            index: 1-based log index.

        Returns:
            LogEntry if found, None if index is out of range.
        """
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None

    def get_term(self, index: int) -> int:
        """Get the term for a specific log index.

        Args:
            index: Log index.

        Returns:
            Term number, or 0 if index is invalid.
        """
        entry = self.get(index)
        return entry.term if entry else 0

    def append(self, entry: LogEntry) -> int:
        """Append an entry to the log.

        Args:
            entry: Entry to append.

        Returns:
            Index of the appended entry.
        """
        entry.index = len(self._entries)
        self._entries.append(entry)
        return entry.index

    def append_entries(
        self,
        prev_index: int,
        prev_term: int,
        entries: list[LogEntry],
    ) -> bool:
        """Append entries from a leader (log matching property).

        Implements the Raft log matching check: if the prev_index
        and prev_term match, append new entries (truncating any
        conflicting entries).

        Args:
            prev_index: Index of the entry before the new ones.
            prev_term: Expected term at prev_index.
            entries: New entries to append.

        Returns:
            True if entries were accepted, False if log doesn't match.
        """
        # Check if we have the entry at prev_index with matching term
        if prev_index > 0:
            if prev_index >= len(self._entries):
                return False
            if self._entries[prev_index].term != prev_term:
                return False

        # Truncate conflicting entries and append new ones
        for i, entry in enumerate(entries):
            log_index = prev_index + 1 + i
            if log_index < len(self._entries):
                if self._entries[log_index].term != entry.term:
                    # Conflict: truncate from here
                    self._entries = self._entries[:log_index]
                    entry.index = log_index
                    self._entries.append(entry)
                # Same term -- entry already exists, skip
            else:
                entry.index = log_index
                self._entries.append(entry)

        return True

    def entries_from(self, start_index: int) -> list[LogEntry]:
        """Get all entries from start_index onwards.

        Args:
            start_index: Starting index (inclusive).

        Returns:
            List of log entries.
        """
        if start_index >= len(self._entries):
            return []
        return list(self._entries[start_index:])

    def __len__(self) -> int:
        """Total number of entries including sentinel."""
        return len(self._entries)


class RaftNode:
    """A single Raft consensus node.

    Implements leader election and log replication. Each node
    maintains its own log and state machine, with the leader
    coordinating replication to achieve consensus.

    Args:
        node_id: Unique identifier for this node.
        config: Raft cluster configuration.
    """

    def __init__(self, node_id: str, config: RaftConfig) -> None:
        self._node_id = node_id
        self._config = config

        # Persistent state
        self._current_term: int = 0
        self._voted_for: Optional[str] = None
        self._log = RaftLog()

        # Volatile state
        self._state = NodeState.FOLLOWER
        self._commit_index: int = 0
        self._last_applied: int = 0
        self._leader_id: Optional[str] = None

        # Leader-only state (reinitialized on election)
        self._next_index: dict[str, int] = {}
        self._match_index: dict[str, int] = {}

        # State machine
        self._state_machine = StateMachine()

        logger.debug("Node %s initialized as follower", node_id)

    @property
    def node_id(self) -> str:
        """This node's unique ID."""
        return self._node_id

    @property
    def state(self) -> NodeState:
        """Current Raft state (follower/candidate/leader)."""
        return self._state

    @property
    def current_term(self) -> int:
        """Current term number."""
        return self._current_term

    @property
    def leader_id(self) -> Optional[str]:
        """ID of the current leader (if known)."""
        return self._leader_id

    @property
    def commit_index(self) -> int:
        """Highest committed log index."""
        return self._commit_index

    @property
    def state_machine(self) -> StateMachine:
        """Access the node's state machine."""
        return self._state_machine

    @property
    def log(self) -> RaftLog:
        """Access the node's log."""
        return self._log

    def is_leader(self) -> bool:
        """Check if this node is the current leader.

        Returns:
            True if this node is the leader.
        """
        return self._state == NodeState.LEADER

    # ── Election ──────────────────────────────────────────────

    def start_election(self, peer_ids: list[str]) -> RequestVoteRequest:
        """Start a leader election.

        Transitions to candidate state, increments term,
        and votes for self.

        Args:
            peer_ids: IDs of all other nodes in the cluster.

        Returns:
            RequestVote RPC to send to all peers.
        """
        self._current_term += 1
        self._state = NodeState.CANDIDATE
        self._voted_for = self._node_id
        self._leader_id = None

        logger.info(
            "Node %s starting election for term %d",
            self._node_id,
            self._current_term,
        )

        return RequestVoteRequest(
            term=self._current_term,
            candidate_id=self._node_id,
            last_log_index=self._log.last_index,
            last_log_term=self._log.last_term,
        )

    def handle_request_vote(
        self, request: RequestVoteRequest
    ) -> RequestVoteResponse:
        """Handle a RequestVote RPC from a candidate.

        Implements Raft's voting rules:
        - Only vote if candidate's term >= our term
        - Only vote if we haven't voted or already voted for this candidate
        - Only vote if candidate's log is at least as up-to-date

        Args:
            request: The vote request.

        Returns:
            Vote response.
        """
        # If request term > our term, update and become follower
        if request.term > self._current_term:
            self._step_down(request.term)

        vote_granted = False

        if request.term >= self._current_term:
            can_vote = (
                self._voted_for is None
                or self._voted_for == request.candidate_id
            )
            log_up_to_date = self._is_log_up_to_date(
                request.last_log_index, request.last_log_term
            )

            if can_vote and log_up_to_date:
                self._voted_for = request.candidate_id
                vote_granted = True
                logger.debug(
                    "Node %s voted for %s in term %d",
                    self._node_id,
                    request.candidate_id,
                    request.term,
                )

        return RequestVoteResponse(
            term=self._current_term,
            vote_granted=vote_granted,
        )

    def become_leader(self, peer_ids: list[str]) -> None:
        """Transition to leader state after winning an election.

        Initializes next_index and match_index for all peers.

        Args:
            peer_ids: IDs of all peer nodes.
        """
        self._state = NodeState.LEADER
        self._leader_id = self._node_id

        # Initialize leader volatile state
        next_idx = self._log.last_index + 1
        self._next_index = {peer: next_idx for peer in peer_ids}
        self._match_index = {peer: 0 for peer in peer_ids}

        # Append a NOOP entry to commit entries from previous terms
        noop = LogEntry(term=self._current_term, command_type=CommandType.NOOP)
        self._log.append(noop)

        logger.info(
            "Node %s became leader for term %d",
            self._node_id,
            self._current_term,
        )

    # ── Log Replication ──────────────────────────────────────

    def propose(self, entry: LogEntry) -> int:
        """Propose a new log entry (leader only).

        Args:
            entry: The entry to propose.

        Returns:
            Index of the proposed entry.

        Raises:
            NotLeaderError: If this node is not the leader.
        """
        if not self.is_leader():
            raise NotLeaderError(self._leader_id)

        entry.term = self._current_term
        index = self._log.append(entry)
        logger.debug(
            "Leader %s proposed entry at index %d",
            self._node_id,
            index,
        )
        return index

    def create_append_entries(
        self, peer_id: str
    ) -> AppendEntriesRequest:
        """Create an AppendEntries RPC for a specific peer.

        Args:
            peer_id: Target peer node ID.

        Returns:
            AppendEntries request with entries to replicate.
        """
        next_idx = self._next_index.get(peer_id, 1)
        prev_idx = next_idx - 1
        prev_term = self._log.get_term(prev_idx)

        entries = self._log.entries_from(next_idx)
        # Limit entries per RPC
        max_entries = self._config.max_log_entries_per_append
        entries = entries[:max_entries]

        return AppendEntriesRequest(
            term=self._current_term,
            leader_id=self._node_id,
            prev_log_index=prev_idx,
            prev_log_term=prev_term,
            entries=entries,
            leader_commit=self._commit_index,
        )

    def handle_append_entries(
        self, request: AppendEntriesRequest
    ) -> AppendEntriesResponse:
        """Handle an AppendEntries RPC (follower).

        Args:
            request: The replication/heartbeat request.

        Returns:
            Response indicating success or failure.
        """
        # If leader's term is stale, reject
        if request.term < self._current_term:
            return AppendEntriesResponse(
                term=self._current_term, success=False
            )

        # Update term if needed
        if request.term > self._current_term:
            self._step_down(request.term)

        # Accept leader
        self._state = NodeState.FOLLOWER
        self._leader_id = request.leader_id

        # Try to append entries
        success = self._log.append_entries(
            request.prev_log_index,
            request.prev_log_term,
            request.entries,
        )

        if not success:
            return AppendEntriesResponse(
                term=self._current_term,
                success=False,
                match_index=0,
            )

        # Update commit index
        if request.leader_commit > self._commit_index:
            self._commit_index = min(
                request.leader_commit, self._log.last_index
            )
            self._apply_committed()

        return AppendEntriesResponse(
            term=self._current_term,
            success=True,
            match_index=self._log.last_index,
        )

    def handle_append_response(
        self, peer_id: str, response: AppendEntriesResponse
    ) -> None:
        """Process an AppendEntries response (leader only).

        Updates next_index and match_index for the peer.

        Args:
            peer_id: The responding peer's ID.
            response: The peer's response.
        """
        if response.term > self._current_term:
            self._step_down(response.term)
            return

        if response.success:
            self._match_index[peer_id] = response.match_index
            self._next_index[peer_id] = response.match_index + 1
        else:
            # Decrement next_index and retry
            current = self._next_index.get(peer_id, 1)
            self._next_index[peer_id] = max(1, current - 1)

    def try_advance_commit(self, peer_ids: list[str]) -> bool:
        """Try to advance the commit index based on majority replication.

        A log entry is committed when replicated to a majority of nodes.

        Args:
            peer_ids: All peer node IDs.

        Returns:
            True if commit index was advanced.
        """
        if not self.is_leader():
            return False

        old_commit = self._commit_index

        # Find the highest index replicated on a majority
        for n in range(self._log.last_index, self._commit_index, -1):
            if self._log.get_term(n) != self._current_term:
                continue

            # Count replications (leader counts as 1)
            replicated = 1
            for peer in peer_ids:
                if self._match_index.get(peer, 0) >= n:
                    replicated += 1

            if replicated >= majority_count(len(peer_ids) + 1):
                self._commit_index = n
                self._apply_committed()
                break

        return self._commit_index > old_commit

    # ── Internal ──────────────────────────────────────────────

    def _step_down(self, new_term: int) -> None:
        """Step down to follower state with a new term.

        Args:
            new_term: The higher term observed.
        """
        self._current_term = new_term
        self._state = NodeState.FOLLOWER
        self._voted_for = None
        self._leader_id = None

    def _is_log_up_to_date(
        self, last_index: int, last_term: int
    ) -> bool:
        """Check if a candidate's log is at least as up-to-date as ours.

        Args:
            last_index: Candidate's last log index.
            last_term: Candidate's last log term.

        Returns:
            True if the candidate's log is at least as current.
        """
        our_last_term = self._log.last_term
        if last_term != our_last_term:
            return last_term > our_last_term
        return last_index >= self._log.last_index

    def _apply_committed(self) -> None:
        """Apply all committed but unapplied entries to the state machine."""
        while self._last_applied < self._commit_index:
            self._last_applied += 1
            entry = self._log.get(self._last_applied)
            if entry:
                self._state_machine.apply(entry)


class RaftCluster:
    """In-process Raft cluster for testing and demonstration.

    Simulates a multi-node Raft cluster within a single process.
    Messages are passed directly between nodes (no network).

    Args:
        config: Raft cluster configuration.
    """

    def __init__(self, config: RaftConfig | None = None) -> None:
        self._config = config or RaftConfig()
        self._nodes: dict[str, RaftNode] = {}

        for i in range(self._config.cluster_size):
            node_id = f"node-{i}"
            self._nodes[node_id] = RaftNode(node_id, self._config)

        self._leader_id: Optional[str] = None
        logger.info(
            "Cluster created with %d nodes", self._config.cluster_size
        )

    @property
    def nodes(self) -> dict[str, RaftNode]:
        """Access all nodes."""
        return self._nodes

    @property
    def leader(self) -> Optional[RaftNode]:
        """Get the current leader node."""
        if self._leader_id and self._leader_id in self._nodes:
            return self._nodes[self._leader_id]
        return None

    def elect_leader(self, candidate_id: Optional[str] = None) -> str:
        """Run a leader election.

        Simulates the election process by having a candidate
        request votes from all peers.

        Args:
            candidate_id: ID of the node to become candidate.
                         Uses first node if not specified.

        Returns:
            ID of the elected leader.

        Raises:
            ElectionError: If election fails.
        """
        if candidate_id is None:
            candidate_id = list(self._nodes.keys())[0]

        candidate = self._nodes[candidate_id]
        peer_ids = [nid for nid in self._nodes if nid != candidate_id]

        # Start election
        vote_request = candidate.start_election(peer_ids)

        # Collect votes
        votes = 1  # Candidate votes for itself
        for peer_id in peer_ids:
            peer = self._nodes[peer_id]
            response = peer.handle_request_vote(vote_request)
            if response.vote_granted:
                votes += 1

        # Check majority
        if votes >= majority_count(len(self._nodes)):
            candidate.become_leader(peer_ids)
            self._leader_id = candidate_id
            logger.info(
                "Election complete: %s is leader (term %d, %d votes)",
                candidate_id,
                candidate.current_term,
                votes,
            )
            return candidate_id
        else:
            raise ElectionError(
                f"Election failed: {candidate_id} got {votes}/{len(self._nodes)} votes"
            )

    def put(self, key: str, value: str) -> None:
        """Write a key-value pair (goes through leader).

        Args:
            key: Key to set.
            value: Value to set.

        Raises:
            NotLeaderError: If no leader is elected.
            ConsensusError: If replication fails.
        """
        leader = self._get_leader()

        entry = LogEntry(
            command_type=CommandType.SET,
            key=key,
            value=value,
        )
        leader.propose(entry)
        self._replicate_and_commit()

    def batch_put(self, data: dict[str, str]) -> None:
        """Write multiple key-value pairs in one operation.

        Args:
            data: Dictionary of key-value pairs.

        Raises:
            NotLeaderError: If no leader is elected.
        """
        leader = self._get_leader()

        entry = LogEntry(
            command_type=CommandType.BATCH_SET,
            batch_data=data,
        )
        leader.propose(entry)
        self._replicate_and_commit()

    def get(self, key: str) -> Optional[str]:
        """Read a value from the leader's state machine.

        Args:
            key: Key to look up.

        Returns:
            Value if found, None otherwise.
        """
        leader = self._get_leader()
        return leader.state_machine.get(key)

    def delete(self, key: str) -> None:
        """Delete a key from the store.

        Args:
            key: Key to delete.
        """
        leader = self._get_leader()

        entry = LogEntry(
            command_type=CommandType.DELETE,
            key=key,
        )
        leader.propose(entry)
        self._replicate_and_commit()

    def keys(self) -> list[str]:
        """Get all keys from the leader's state machine.

        Returns:
            List of keys.
        """
        leader = self._get_leader()
        return leader.state_machine.keys()

    def status(self) -> ClusterStatus:
        """Get current cluster status.

        Returns:
            ClusterStatus with current state information.
        """
        leader = self.leader
        return ClusterStatus(
            leader_id=self._leader_id or "",
            term=leader.current_term if leader else 0,
            num_nodes=len(self._nodes),
            committed_index=leader.commit_index if leader else 0,
            total_entries=sum(
                len(node.log) for node in self._nodes.values()
            ),
            state_machine_size=(
                leader.state_machine.size if leader else 0
            ),
        )

    def _get_leader(self) -> RaftNode:
        """Get the current leader or raise an error.

        Returns:
            The leader node.

        Raises:
            NotLeaderError: If no leader is elected.
        """
        if self._leader_id is None or self._leader_id not in self._nodes:
            raise NotLeaderError()
        return self._nodes[self._leader_id]

    def _replicate_and_commit(self) -> None:
        """Replicate entries from leader to all followers and commit.

        This simulates the full replication cycle synchronously.
        """
        leader = self._get_leader()
        peer_ids = [nid for nid in self._nodes if nid != self._leader_id]

        for peer_id in peer_ids:
            request = leader.create_append_entries(peer_id)
            peer = self._nodes[peer_id]
            response = peer.handle_append_entries(request)
            leader.handle_append_response(peer_id, response)

        leader.try_advance_commit(peer_ids)
