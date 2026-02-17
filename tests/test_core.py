"""Tests for core Raft consensus implementation."""

from __future__ import annotations

import pytest

from distributed_kv_store.core import RaftCluster, RaftLog, RaftNode, StateMachine
from distributed_kv_store.exceptions import (
    ConsensusError,
    ElectionError,
    NotLeaderError,
)
from distributed_kv_store.models import (
    AppendEntriesRequest,
    CommandType,
    LogEntry,
    NodeState,
    RaftConfig,
    RequestVoteRequest,
)


# ── StateMachine Tests ─────────────────────────────────────────────────────────


class TestStateMachine:
    """Tests for the key-value state machine."""

    def test_set_and_get(self, state_machine: StateMachine) -> None:
        entry = LogEntry(command_type=CommandType.SET, key="k", value="v")
        state_machine.apply(entry)
        assert state_machine.get("k") == "v"

    def test_get_missing_key(self, state_machine: StateMachine) -> None:
        assert state_machine.get("nonexistent") is None

    def test_delete(self, state_machine: StateMachine) -> None:
        state_machine.apply(
            LogEntry(command_type=CommandType.SET, key="k", value="v")
        )
        state_machine.apply(
            LogEntry(command_type=CommandType.DELETE, key="k")
        )
        assert state_machine.get("k") is None

    def test_delete_nonexistent(self, state_machine: StateMachine) -> None:
        """Deleting a missing key should not raise."""
        state_machine.apply(
            LogEntry(command_type=CommandType.DELETE, key="nope")
        )
        assert state_machine.size == 0

    def test_batch_set(self, state_machine: StateMachine) -> None:
        entry = LogEntry(
            command_type=CommandType.BATCH_SET,
            batch_data={"a": "1", "b": "2", "c": "3"},
        )
        state_machine.apply(entry)
        assert state_machine.get("a") == "1"
        assert state_machine.get("b") == "2"
        assert state_machine.size == 3

    def test_batch_delete(self, state_machine: StateMachine) -> None:
        state_machine.apply(
            LogEntry(
                command_type=CommandType.BATCH_SET,
                batch_data={"x": "1", "y": "2", "z": "3"},
            )
        )
        state_machine.apply(
            LogEntry(
                command_type=CommandType.BATCH_DELETE,
                batch_data={"x": "", "z": ""},
            )
        )
        assert state_machine.get("x") is None
        assert state_machine.get("y") == "2"
        assert state_machine.size == 1

    def test_noop(self, state_machine: StateMachine) -> None:
        state_machine.apply(LogEntry(command_type=CommandType.NOOP))
        assert state_machine.size == 0
        assert state_machine.version == 1

    def test_version_increments(self, state_machine: StateMachine) -> None:
        for i in range(5):
            state_machine.apply(
                LogEntry(command_type=CommandType.SET, key=f"k{i}", value=f"v{i}")
            )
        assert state_machine.version == 5

    def test_keys(self, state_machine: StateMachine) -> None:
        state_machine.apply(
            LogEntry(
                command_type=CommandType.BATCH_SET,
                batch_data={"alpha": "1", "beta": "2"},
            )
        )
        keys = state_machine.keys()
        assert set(keys) == {"alpha", "beta"}

    def test_get_all(self, state_machine: StateMachine) -> None:
        state_machine.apply(
            LogEntry(command_type=CommandType.SET, key="k", value="v")
        )
        snapshot = state_machine.get_all()
        assert snapshot == {"k": "v"}
        # Ensure it's a copy
        snapshot["k"] = "modified"
        assert state_machine.get("k") == "v"

    def test_snapshot_and_restore(self, state_machine: StateMachine) -> None:
        state_machine.apply(
            LogEntry(command_type=CommandType.SET, key="a", value="1")
        )
        state_machine.apply(
            LogEntry(command_type=CommandType.SET, key="b", value="2")
        )
        snap = state_machine.snapshot()

        new_sm = StateMachine()
        new_sm.restore(snap)
        assert new_sm.get("a") == "1"
        assert new_sm.get("b") == "2"
        assert new_sm.version == 2

    @pytest.mark.parametrize(
        "key,value",
        [
            ("simple", "value"),
            ("with spaces", "also spaces"),
            ("unicode_key_\u00e9", "unicode_val_\u00f1"),
            ("", "empty_key"),
            ("long_" * 100, "long_value"),
        ],
    )
    def test_various_key_formats(
        self, state_machine: StateMachine, key: str, value: str
    ) -> None:
        state_machine.apply(
            LogEntry(command_type=CommandType.SET, key=key, value=value)
        )
        assert state_machine.get(key) == value


# ── RaftLog Tests ──────────────────────────────────────────────────────────────


class TestRaftLog:
    """Tests for the Raft log."""

    def test_initial_state(self, raft_log: RaftLog) -> None:
        assert raft_log.last_index == 0
        assert raft_log.last_term == 0
        assert len(raft_log) == 1  # sentinel

    def test_append(self, raft_log: RaftLog) -> None:
        entry = LogEntry(term=1, command_type=CommandType.SET, key="k", value="v")
        idx = raft_log.append(entry)
        assert idx == 1
        assert raft_log.last_index == 1
        assert raft_log.last_term == 1

    def test_get_valid_index(self, raft_log: RaftLog) -> None:
        raft_log.append(LogEntry(term=1, command_type=CommandType.SET, key="k", value="v"))
        entry = raft_log.get(1)
        assert entry is not None
        assert entry.key == "k"

    def test_get_invalid_index(self, raft_log: RaftLog) -> None:
        assert raft_log.get(99) is None
        assert raft_log.get(-1) is None

    def test_get_term(self, raft_log: RaftLog) -> None:
        raft_log.append(LogEntry(term=3))
        assert raft_log.get_term(1) == 3
        assert raft_log.get_term(99) == 0

    def test_entries_from(self, raft_log: RaftLog) -> None:
        for i in range(1, 4):
            raft_log.append(LogEntry(term=i, command_type=CommandType.SET, key=f"k{i}"))
        entries = raft_log.entries_from(2)
        assert len(entries) == 2
        assert entries[0].term == 2
        assert entries[1].term == 3

    def test_entries_from_beyond_end(self, raft_log: RaftLog) -> None:
        assert raft_log.entries_from(100) == []

    def test_append_entries_success(self, raft_log: RaftLog) -> None:
        # Add an entry at index 1
        raft_log.append(LogEntry(term=1))
        # Append with matching prev
        new_entries = [LogEntry(term=1), LogEntry(term=1)]
        result = raft_log.append_entries(1, 1, new_entries)
        assert result is True
        assert raft_log.last_index == 3

    def test_append_entries_log_mismatch(self, raft_log: RaftLog) -> None:
        raft_log.append(LogEntry(term=1))
        # Wrong prev_term
        result = raft_log.append_entries(1, 99, [LogEntry(term=2)])
        assert result is False

    def test_append_entries_gap(self, raft_log: RaftLog) -> None:
        # prev_index beyond our log
        result = raft_log.append_entries(5, 1, [LogEntry(term=1)])
        assert result is False

    def test_append_entries_truncates_conflict(self, raft_log: RaftLog) -> None:
        """Conflicting entries should be truncated."""
        raft_log.append(LogEntry(term=1))
        raft_log.append(LogEntry(term=1))
        raft_log.append(LogEntry(term=1))
        # Overwrite from index 2 with term=2 entries
        result = raft_log.append_entries(1, 1, [LogEntry(term=2), LogEntry(term=2)])
        assert result is True
        assert raft_log.last_index == 3
        assert raft_log.get_term(2) == 2
        assert raft_log.get_term(3) == 2

    def test_append_entries_empty(self, raft_log: RaftLog) -> None:
        """Empty entries (heartbeat) should succeed."""
        result = raft_log.append_entries(0, 0, [])
        assert result is True


# ── RaftNode Tests ─────────────────────────────────────────────────────────────


class TestRaftNode:
    """Tests for a single Raft node."""

    def test_initial_state(self, single_node: RaftNode) -> None:
        assert single_node.state == NodeState.FOLLOWER
        assert single_node.current_term == 0
        assert single_node.leader_id is None
        assert not single_node.is_leader()

    def test_start_election(self, single_node: RaftNode) -> None:
        request = single_node.start_election(["node-1", "node-2"])
        assert single_node.state == NodeState.CANDIDATE
        assert single_node.current_term == 1
        assert request.term == 1
        assert request.candidate_id == "node-0"

    def test_vote_granted(self, config: RaftConfig) -> None:
        voter = RaftNode("voter", config)
        request = RequestVoteRequest(
            term=1, candidate_id="candidate",
            last_log_index=0, last_log_term=0,
        )
        response = voter.handle_request_vote(request)
        assert response.vote_granted
        assert response.term == 1

    def test_vote_denied_already_voted(self, config: RaftConfig) -> None:
        voter = RaftNode("voter", config)
        # Vote for first candidate
        req1 = RequestVoteRequest(term=1, candidate_id="c1")
        voter.handle_request_vote(req1)
        # Second candidate in same term
        req2 = RequestVoteRequest(term=1, candidate_id="c2")
        response = voter.handle_request_vote(req2)
        assert not response.vote_granted

    def test_vote_denied_stale_term(self, config: RaftConfig) -> None:
        voter = RaftNode("voter", config)
        # Advance voter's term
        voter.handle_request_vote(
            RequestVoteRequest(term=5, candidate_id="x")
        )
        # Request with old term
        response = voter.handle_request_vote(
            RequestVoteRequest(term=3, candidate_id="y")
        )
        assert not response.vote_granted

    def test_become_leader(self, single_node: RaftNode) -> None:
        peers = ["node-1", "node-2"]
        single_node.start_election(peers)
        single_node.become_leader(peers)
        assert single_node.is_leader()
        assert single_node.state == NodeState.LEADER
        assert single_node.leader_id == "node-0"

    def test_propose_as_leader(self, single_node: RaftNode) -> None:
        peers = ["node-1", "node-2"]
        single_node.start_election(peers)
        single_node.become_leader(peers)
        entry = LogEntry(command_type=CommandType.SET, key="k", value="v")
        idx = single_node.propose(entry)
        # sentinel(0) + NOOP from become_leader(1) -> propose at 2
        assert idx == 2

    def test_propose_not_leader_raises(self, single_node: RaftNode) -> None:
        with pytest.raises(NotLeaderError):
            single_node.propose(LogEntry(command_type=CommandType.SET, key="k", value="v"))

    def test_handle_append_entries_heartbeat(self, config: RaftConfig) -> None:
        follower = RaftNode("follower", config)
        request = AppendEntriesRequest(
            term=1, leader_id="leader",
            prev_log_index=0, prev_log_term=0,
            entries=[], leader_commit=0,
        )
        response = follower.handle_append_entries(request)
        assert response.success
        assert follower.leader_id == "leader"

    def test_handle_append_entries_stale_term(self, config: RaftConfig) -> None:
        follower = RaftNode("follower", config)
        # Advance term
        follower.handle_request_vote(
            RequestVoteRequest(term=5, candidate_id="x")
        )
        # Stale append
        request = AppendEntriesRequest(term=3, leader_id="old_leader")
        response = follower.handle_append_entries(request)
        assert not response.success

    def test_step_down_on_higher_term(self, single_node: RaftNode) -> None:
        peers = ["node-1", "node-2"]
        single_node.start_election(peers)
        single_node.become_leader(peers)
        assert single_node.is_leader()

        # Higher-term append should step down
        request = AppendEntriesRequest(
            term=99, leader_id="new_leader",
            prev_log_index=0, prev_log_term=0,
        )
        single_node.handle_append_entries(request)
        assert single_node.state == NodeState.FOLLOWER
        assert single_node.current_term == 99


# ── RaftCluster Tests ──────────────────────────────────────────────────────────


class TestRaftCluster:
    """Tests for the in-process Raft cluster."""

    def test_cluster_creation(self, cluster: RaftCluster) -> None:
        assert len(cluster.nodes) == 3
        assert cluster.leader is None

    def test_elect_leader(self, cluster: RaftCluster) -> None:
        leader_id = cluster.elect_leader()
        assert leader_id == "node-0"
        assert cluster.leader is not None
        assert cluster.leader.is_leader()

    def test_elect_specific_leader(self, cluster: RaftCluster) -> None:
        leader_id = cluster.elect_leader("node-2")
        assert leader_id == "node-2"

    def test_put_and_get(self, elected_cluster: RaftCluster) -> None:
        elected_cluster.put("key1", "value1")
        assert elected_cluster.get("key1") == "value1"

    def test_get_missing_key(self, elected_cluster: RaftCluster) -> None:
        assert elected_cluster.get("missing") is None

    def test_delete(self, elected_cluster: RaftCluster) -> None:
        elected_cluster.put("to_delete", "val")
        elected_cluster.delete("to_delete")
        assert elected_cluster.get("to_delete") is None

    def test_batch_put(self, elected_cluster: RaftCluster) -> None:
        data = {"a": "1", "b": "2", "c": "3"}
        elected_cluster.batch_put(data)
        assert elected_cluster.get("a") == "1"
        assert elected_cluster.get("b") == "2"
        assert elected_cluster.get("c") == "3"

    def test_keys(self, elected_cluster: RaftCluster) -> None:
        elected_cluster.put("x", "1")
        elected_cluster.put("y", "2")
        keys = elected_cluster.keys()
        assert set(keys) == {"x", "y"}

    def test_status(self, elected_cluster: RaftCluster) -> None:
        elected_cluster.put("k", "v")
        status = elected_cluster.status()
        assert status.leader_id == "node-0"
        assert status.num_nodes == 3
        assert status.term >= 1
        assert status.state_machine_size >= 1

    def test_put_without_leader_raises(self, cluster: RaftCluster) -> None:
        with pytest.raises(NotLeaderError):
            cluster.put("k", "v")

    def test_multiple_writes(self, elected_cluster: RaftCluster) -> None:
        for i in range(20):
            elected_cluster.put(f"key_{i}", f"value_{i}")
        for i in range(20):
            assert elected_cluster.get(f"key_{i}") == f"value_{i}"
        assert len(elected_cluster.keys()) == 20

    def test_overwrite_value(self, elected_cluster: RaftCluster) -> None:
        elected_cluster.put("k", "v1")
        elected_cluster.put("k", "v2")
        assert elected_cluster.get("k") == "v2"

    def test_five_node_cluster(self) -> None:
        config = RaftConfig(cluster_size=5)
        cluster = RaftCluster(config)
        leader_id = cluster.elect_leader()
        assert leader_id is not None
        cluster.put("test", "value")
        assert cluster.get("test") == "value"

    def test_replication_consistency(self, elected_cluster: RaftCluster) -> None:
        """All nodes should have the same committed entries."""
        elected_cluster.put("k1", "v1")
        elected_cluster.put("k2", "v2")

        leader = elected_cluster.leader
        assert leader is not None
        for node_id, node in elected_cluster.nodes.items():
            # All nodes should have the same log length
            assert len(node.log) == len(leader.log), (
                f"Node {node_id} log length mismatch"
            )
