# Architecture: Distributed KV Store

## Overview

A Raft-consensus key-value store optimized for ML metadata patterns. The system guarantees linearizable writes through leader-based log replication and majority commitment.

## Component Architecture

```
┌─────────────────────────────────────────────────┐
│                  RaftCluster                     │
│  (in-process cluster orchestrator)               │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ RaftNode  │  │ RaftNode  │  │ RaftNode  │      │
│  │ (Leader)  │  │ (Follower)│  │ (Follower)│      │
│  │           │  │           │  │           │      │
│  │ ┌───────┐│  │ ┌───────┐│  │ ┌───────┐│      │
│  │ │RaftLog││  │ │RaftLog││  │ │RaftLog││      │
│  │ └───────┘│  │ └───────┘│  │ └───────┘│      │
│  │ ┌───────┐│  │ ┌───────┐│  │ ┌───────┐│      │
│  │ │  SM   ││  │ │  SM   ││  │ │  SM   ││      │
│  │ └───────┘│  │ └───────┘│  │ └───────┘│      │
│  └──────────┘  └──────────┘  └──────────┘      │
└─────────────────────────────────────────────────┘
```

## Data Flow

### Write Path

1. Client calls `cluster.put(key, value)`
2. Leader creates a `LogEntry` with `CommandType.SET`
3. Leader proposes entry (appends to local log)
4. Leader sends `AppendEntries` RPCs to all followers
5. Followers validate log matching and append entries
6. Leader counts successful replications
7. If majority replicated, entry is committed
8. State machine applies the committed entry

### Read Path

1. Client calls `cluster.get(key)`
2. Leader reads directly from its local `StateMachine`
3. No consensus round needed for reads (leader-local)

### Election

1. Follower detects leader timeout
2. Increments term, transitions to CANDIDATE
3. Sends `RequestVote` RPCs to all peers
4. Peers grant vote if: term is current, haven't voted, candidate log is up-to-date
5. If majority votes received, becomes LEADER
6. Appends NOOP entry to commit pending entries from prior terms

## Component Details

### StateMachine

- Pure key-value store (dict-based)
- Supports: SET, DELETE, BATCH_SET, BATCH_DELETE, NOOP
- Deterministic application -- same log produces same state
- Snapshot/restore for state transfer

### RaftLog

- 1-indexed with sentinel at position 0
- Implements log matching property (Section 5.3 of Raft paper)
- Truncates conflicting entries on replication
- Entries are immutable once committed

### RaftNode

- Implements the full Raft state machine (follower/candidate/leader)
- Persistent state: currentTerm, votedFor, log
- Volatile state: commitIndex, lastApplied, nextIndex[], matchIndex[]
- Step-down on higher term (Section 5.1)
- Log up-to-date check for vote granting (Section 5.4.1)

### RaftCluster

- In-process simulation (no network layer)
- Synchronous replication for deterministic testing
- Supports 1-9 node clusters (odd numbers recommended)

## Key Design Decisions

| Decision                 | Rationale                                   | Alternative           |
| ------------------------ | ------------------------------------------- | --------------------- |
| In-process cluster       | Deterministic testing, no network flakiness | gRPC/TCP cluster      |
| Synchronous replication  | Predictable behavior for demonstration      | Async with timeouts   |
| Dict-based state machine | Simple, fast for metadata use case          | LSM tree / B-tree     |
| Dataclasses for hot path | Lower overhead than Pydantic for RPCs       | Pydantic everywhere   |
| Pydantic for config      | Validation on cluster creation              | Plain dataclasses     |
| 1-indexed log            | Matches Raft paper convention               | 0-indexed             |
| Sentinel entry           | Simplifies prev_log_index=0 edge case       | Special-case handling |
