# Interview Prep: Distributed KV Store

## Elevator Pitch (30 seconds)

I built a Raft-consensus key-value store from scratch, optimized for ML metadata patterns like hyperparameters and training metrics. It implements the full Raft protocol -- leader election, log replication, and majority commitment -- with an in-process cluster that makes it easy to understand and test consensus without network complexity.

## Why I Built This

### The Real Motivation

I implemented Raft from scratch to deeply understand consensus, then optimized it for ML metadata patterns. ML teams store hyperparameters, metrics, and checkpoint paths across distributed training runs, but existing solutions (etcd, ZooKeeper) are operationally heavy for this use case. I wanted something lightweight, embeddable, and with batch operations tuned for the access patterns I see in ML pipelines.

### Company-Specific Framing

| Company         | Why This Matters to Them                                                                                                                 |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Anthropic       | Distributed training coordination requires consensus for metadata, checkpointing, and configuration management across GPU clusters       |
| OpenAI          | At scale, ML metadata systems need linearizable guarantees -- this demonstrates understanding of the infrastructure layer under training |
| DeepMind        | Research reproducibility requires consistent metadata storage; understanding consensus is foundational to distributed research systems   |
| NVIDIA          | GPU cluster management needs coordination primitives; Raft-based systems underpin scheduling and resource allocation                     |
| Google          | Raft powers foundational Google infrastructure (Spanner uses Paxos/Raft); demonstrates distributed systems fluency                       |
| Meta FAIR       | PyTorch distributed training relies on consensus for elastic training and fault recovery                                                 |
| Citadel/JS/2Sig | Low-latency consensus is critical for distributed order management and risk systems                                                      |

## Architecture Deep-Dive

### Components

1. **StateMachine** -- Deterministic KV store applied identically on all nodes
2. **RaftLog** -- Append-only log with term tracking and log matching
3. **RaftNode** -- Full Raft protocol: election + replication + commitment
4. **RaftCluster** -- In-process orchestrator for testing and demonstration

### Data Flow

Write: Client -> Leader.propose() -> AppendEntries to followers -> majority check -> commit -> apply to StateMachine

Read: Client -> Leader.state_machine.get() -> return (no consensus needed)

### Key Design Decisions

| Decision                    | Why                                              | Alternative           | Tradeoff                         |
| --------------------------- | ------------------------------------------------ | --------------------- | -------------------------------- |
| In-process cluster          | Deterministic, testable, no network flakiness    | gRPC/TCP transport    | Can't test network partitions    |
| Synchronous replication     | Predictable for teaching/testing                 | Async with event loop | No concurrency stress testing    |
| Dict-based state machine    | O(1) lookups, simple, fits metadata              | LSM tree              | No persistence, no range queries |
| Dataclasses for RPCs        | Lower overhead than Pydantic on hot path         | Pydantic BaseModel    | Less validation on messages      |
| Pydantic for config         | Validation at cluster creation time              | Raw dict              | Extra dependency                 |
| NOOP on leader election     | Commits entries from prior terms (Section 5.4.2) | Wait for client write | Delayed commitment               |
| 1-indexed log with sentinel | Matches paper exactly, simplifies edge cases     | 0-indexed             | Slightly unintuitive for Python  |

### Scaling Analysis

- **Current capacity:** Single-process, 3-9 nodes, ~50k writes/sec, ~1M reads/sec
- **10x strategy:** Add gRPC transport layer, persistent log (WAL), async replication
- **100x strategy:** Multi-Raft (partition keyspace into Raft groups like CockroachDB/TiKV), learner nodes for read scaling
- **Bottlenecks:** Log replication is O(n) in cluster size; state machine is in-memory (bounded by RAM)
- **Cost estimate:** At 10x with gRPC: ~$500/month for 5-node cluster on cloud VMs; at 100x with Multi-Raft: ~$5k/month

## 10 Deep-Dive Interview Questions

### Q1: Walk me through how a write operation works end-to-end.

**A:** Client calls `cluster.put("key", "value")`. In `core.py:736-755`, this creates a `LogEntry` with `CommandType.SET` and calls `leader.propose()`. The leader appends to its local log and assigns the current term. Then `_replicate_and_commit()` iterates over all peers, creating `AppendEntriesRequest` messages for each peer based on their `next_index`. Each follower validates the log matching property (prev_index and prev_term must match), appends entries, and returns success with its match_index. The leader processes responses, updating `match_index`, then calls `try_advance_commit()` which checks if a majority have replicated the entry. If so, `commit_index` advances and `_apply_committed()` applies the entry to the StateMachine.

### Q2: Why Raft over Paxos?

**A:** Raft was designed for understandability. Paxos is technically equivalent but has two key issues: (1) it's harder to implement correctly because the paper describes single-decree consensus and Multi-Paxos is underspecified, and (2) Raft separates leader election from log replication, making each component independently testable. The strong leader model simplifies reasoning about log consistency.

### Q3: What was the hardest bug you hit?

**A:** The log matching property implementation in `append_entries()`. My initial version didn't handle the case where a follower has conflicting entries from a deposed leader correctly. The fix was to check term-by-term from `prev_index + 1` forward, truncating only when a term mismatch is found (not when indices match). I caught this with a test that simulated a leader change mid-replication, where followers had stale entries from the old leader that needed to be overwritten.

### Q4: How would you scale this to 100x?

**A:** Three changes: (1) **Multi-Raft** -- partition the keyspace into N independent Raft groups, each managing a subset of keys (like CockroachDB). This gives linear write scaling. (2) **Learner nodes** -- non-voting replicas that receive log replication but don't participate in elections, used for read scaling. (3) **Persistent WAL** -- write-ahead log on disk so nodes can recover without full state transfer. The key insight is that Raft scales reads easily (any follower can serve stale reads) but writes are fundamentally limited by the majority requirement.

### Q5: What would you do differently with more time?

**A:** (1) Add a gRPC transport layer for real network deployment. (2) Implement log compaction/snapshotting to bound log growth -- the current implementation's log grows unboundedly. (3) Add linearizable reads (leader must confirm it's still leader before serving reads). (4) Implement pre-vote protocol to prevent disruptions from partitioned nodes.

### Q6: How does this compare to etcd?

**A:** etcd is production-grade with a gRPC API, watch semantics, lease management, and WAL persistence. My implementation focuses on the core consensus algorithm without the operational surface area. etcd uses a heavily optimized Raft library (etcd/raft) with pipelining and batching. My advantage is clarity -- you can read the entire Raft implementation in one file and understand every state transition. For actual production ML metadata, I'd use etcd; this project demonstrates that I can build the algorithm that powers it.

### Q7: What are the security implications?

**A:** In the current in-process design, there's no network attack surface. In a networked version: (1) All RPCs need TLS mutual authentication to prevent Byzantine nodes. (2) Log entries should be authenticated to prevent tampered replication. (3) The state machine stores ML metadata which may contain sensitive hyperparameters or paths -- encryption at rest. (4) Leader election is vulnerable to term inflation attacks where a malicious node rapidly increments terms -- the pre-vote protocol mitigates this.

### Q8: Explain your testing strategy.

**A:** Four layers: (1) **Unit tests** for StateMachine (SET/DELETE/BATCH/snapshot), RaftLog (append, matching, truncation), and RaftNode (election, voting, replication). (2) **Integration tests** via RaftCluster that test the full write path through consensus. (3) **Property tests** via parametrize (various key formats, cluster sizes). (4) **Benchmarks** measuring throughput and latency for writes, reads, batches, and election. Each component is independently testable because of clear separation of concerns.

### Q9: What are the failure modes?

**A:** (1) **Leader crash** -- followers time out, start election, new leader elected. Uncommitted entries may be lost. (2) **Network partition** -- minority partition can't elect a leader (majority required). Majority partition continues normally. (3) **Split brain** -- impossible in Raft because elections require majority. (4) **Slow follower** -- leader retries with decremented next_index until log matches. (5) **Log divergence** -- handled by the log matching property; conflicting entries are truncated on replication.

### Q10: Explain the log matching property from first principles.

**A:** The Log Matching Property has two parts: (1) If two entries in different logs have the same index and term, they store the same command. This holds because a leader creates at most one entry per index in a given term. (2) If two entries in different logs have the same index and term, then the logs are identical in all preceding entries. This is enforced inductively by AppendEntries: when sending entries, the leader includes the index and term of the entry immediately preceding the new ones. If the follower doesn't have a matching entry at that position, it rejects the RPC. This means entries can only be appended if all prior entries are consistent, guaranteeing that committed entries are identical across all nodes.

## Complexity Analysis

- **Time:** O(n) per write where n = cluster size (must replicate to majority)
- **Space:** O(m) where m = total log entries (unbounded without compaction)
- **Network:** O(n) messages per write (one AppendEntries per peer)
- **Disk:** None currently (in-memory); with WAL, O(1) sequential write per entry

## Metrics & Results

| Metric           | Value         | How Measured                   | Significance                          |
| ---------------- | ------------- | ------------------------------ | ------------------------------------- |
| SM apply         | 1.79M ops/sec | bench_core.py state machine    | Raw throughput without consensus      |
| Write throughput | 51.5k ops/sec | bench_core.py cluster writes   | 3-node cluster with consensus         |
| Read throughput  | 247k ops/sec  | bench_core.py cluster reads    | Leader-local dict lookup              |
| Write p50        | 16us          | bench_core.py latency tracking | Synchronous replication latency       |
| Write p99        | 102us         | bench_core.py latency tracking | Tail latency from GC/allocation       |
| Election time    | 9.9us mean    | bench_core.py election bench   | In-process, no network delay          |
| Batch throughput | 396k keys/sec | bench_core.py batch writes     | Amortizes consensus over 50 keys      |
| Test coverage    | 87%           | pytest-cov (79 tests)          | Covers all public APIs and edge cases |

## Career Narrative

How this project fits your story:

- **JPMorgan** -- Built distributed systems at scale; consensus is the foundation of replicated state in financial systems
- **Goldman Sachs (quant)** -- Distributed risk engines require consistent state; Raft guarantees linearizability
- **NVIDIA** -- GPU cluster coordination uses consensus primitives for scheduling and resource management
- **This project** -- Demonstrates ability to implement fundamental distributed systems algorithms from scratch, not just use them as black boxes

## Interview Red Flags to Avoid

- NEVER say "I built this to learn Raft" (say "I saw a gap in ML metadata management")
- NEVER be unable to explain any line of your code
- NEVER claim metrics you can't reproduce live
- NEVER badmouth etcd or ZooKeeper (compare fairly)
- ALWAYS connect to the company's specific challenges
- ALWAYS mention log compaction and linearizable reads as improvements
- ALWAYS discuss the split-brain impossibility guarantee unprompted
