"""Tests for data models and validation."""

from __future__ import annotations

import pytest

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


class TestNodeState:
    """Tests for NodeState enum."""

    def test_values(self) -> None:
        assert NodeState.FOLLOWER == "follower"
        assert NodeState.CANDIDATE == "candidate"
        assert NodeState.LEADER == "leader"

    def test_all_states_present(self) -> None:
        assert len(NodeState) == 3


class TestCommandType:
    """Tests for CommandType enum."""

    def test_values(self) -> None:
        assert CommandType.SET == "set"
        assert CommandType.DELETE == "delete"
        assert CommandType.BATCH_SET == "batch_set"
        assert CommandType.BATCH_DELETE == "batch_delete"
        assert CommandType.NOOP == "noop"

    def test_all_commands_present(self) -> None:
        assert len(CommandType) == 5


class TestLogEntry:
    """Tests for LogEntry dataclass."""

    def test_defaults(self) -> None:
        entry = LogEntry()
        assert entry.term == 0
        assert entry.index == 0
        assert entry.command_type == CommandType.NOOP
        assert entry.key == ""
        assert entry.value == ""
        assert entry.batch_data == {}

    def test_set_entry(self) -> None:
        entry = LogEntry(
            term=3, index=5, command_type=CommandType.SET,
            key="lr", value="0.001",
        )
        assert entry.term == 3
        assert entry.key == "lr"
        assert entry.value == "0.001"

    def test_to_dict(self) -> None:
        entry = LogEntry(
            term=1, index=2, command_type=CommandType.SET,
            key="k", value="v",
        )
        d = entry.to_dict()
        assert d["term"] == 1
        assert d["index"] == 2
        assert d["command_type"] == "set"
        assert d["key"] == "k"
        assert d["value"] == "v"
        assert d["batch_data"] == {}

    def test_batch_data_isolation(self) -> None:
        """Batch data dict should not be shared between instances."""
        e1 = LogEntry()
        e2 = LogEntry()
        e1.batch_data["x"] = "1"
        assert "x" not in e2.batch_data

    @pytest.mark.parametrize(
        "cmd_type,expected_str",
        [
            (CommandType.SET, "set"),
            (CommandType.DELETE, "delete"),
            (CommandType.BATCH_SET, "batch_set"),
            (CommandType.NOOP, "noop"),
        ],
    )
    def test_command_type_serialization(
        self, cmd_type: CommandType, expected_str: str
    ) -> None:
        entry = LogEntry(command_type=cmd_type)
        assert entry.to_dict()["command_type"] == expected_str


class TestRaftConfig:
    """Tests for RaftConfig validation."""

    def test_defaults(self) -> None:
        config = RaftConfig()
        assert config.cluster_size == 3
        assert config.election_timeout_min_ms == 150
        assert config.election_timeout_max_ms == 300
        assert config.heartbeat_interval_ms == 50
        assert config.max_log_entries_per_append == 100
        assert config.snapshot_threshold == 1000

    def test_custom_values(self) -> None:
        config = RaftConfig(cluster_size=5, heartbeat_interval_ms=25)
        assert config.cluster_size == 5
        assert config.heartbeat_interval_ms == 25

    def test_cluster_size_max(self) -> None:
        with pytest.raises(Exception):
            RaftConfig(cluster_size=11)

    def test_cluster_size_min(self) -> None:
        config = RaftConfig(cluster_size=1)
        assert config.cluster_size == 1

    def test_heartbeat_min(self) -> None:
        with pytest.raises(Exception):
            RaftConfig(heartbeat_interval_ms=5)


class TestAppendEntriesRequest:
    """Tests for AppendEntries RPC request."""

    def test_defaults(self) -> None:
        req = AppendEntriesRequest()
        assert req.term == 0
        assert req.leader_id == ""
        assert req.prev_log_index == 0
        assert req.prev_log_term == 0
        assert req.entries == []
        assert req.leader_commit == 0

    def test_with_entries(self) -> None:
        entries = [LogEntry(term=1, command_type=CommandType.SET, key="a", value="b")]
        req = AppendEntriesRequest(term=1, leader_id="n0", entries=entries)
        assert len(req.entries) == 1
        assert req.entries[0].key == "a"


class TestAppendEntriesResponse:
    """Tests for AppendEntries RPC response."""

    def test_success(self) -> None:
        resp = AppendEntriesResponse(term=1, success=True, match_index=5)
        assert resp.success
        assert resp.match_index == 5

    def test_failure(self) -> None:
        resp = AppendEntriesResponse(term=2, success=False)
        assert not resp.success


class TestRequestVote:
    """Tests for RequestVote RPC messages."""

    def test_request_defaults(self) -> None:
        req = RequestVoteRequest()
        assert req.term == 0
        assert req.candidate_id == ""

    def test_response_grant(self) -> None:
        resp = RequestVoteResponse(term=1, vote_granted=True)
        assert resp.vote_granted

    def test_response_deny(self) -> None:
        resp = RequestVoteResponse(term=1, vote_granted=False)
        assert not resp.vote_granted


class TestClusterStatus:
    """Tests for ClusterStatus."""

    def test_defaults(self) -> None:
        status = ClusterStatus()
        assert status.leader_id == ""
        assert status.term == 0
        assert status.num_nodes == 0

    def test_populated(self) -> None:
        status = ClusterStatus(
            leader_id="node-0", term=3, num_nodes=5,
            committed_index=42, total_entries=150,
            state_machine_size=30,
        )
        assert status.leader_id == "node-0"
        assert status.committed_index == 42
