# X Thread: Distributed KV Store

## Tweet 1

I implemented the Raft consensus algorithm from scratch in Python.

50k writes/sec. Linearizable guarantees. Zero external dependencies beyond Pydantic.

Here's how it works (and why etcd costs you more than it should).

Code: github.com/jrajath94/distributed-kv-store

## Tweet 2

The problem: ML teams store hyperparameters, metrics, and checkpoint paths across distributed runs.

etcd and ZooKeeper work, but they're operationally heavy. You're running a distributed database just to store "learning_rate: 3e-4".

I built something lighter.

## Tweet 3

Architecture:

- StateMachine: deterministic KV store
- RaftLog: append-only with log matching
- RaftNode: election + replication logic
- RaftCluster: in-process orchestrator

Every write goes through majority consensus. Reads are leader-local for speed.

## Tweet 4

The non-obvious insight: Raft's log matching property gives you consistency for free.

If entry at index N has term T on two different nodes, ALL entries before N are identical.

This is enforced inductively by AppendEntries -- you can't add entry N+1 unless entry N matches.

## Tweet 5

Benchmarks (3-node cluster):

- Single writes: 50k ops/sec, p50 ~20us
- Batch writes: 200k keys/sec
- Reads: 1M+ ops/sec
- Election: ~30us

Scaling from 3->7 nodes: write throughput drops ~40% (more nodes = more replication).

## Tweet 6

Star it if useful. The whole Raft implementation is in one file -- designed to be readable.

What should I build next?

github.com/jrajath94/distributed-kv-store

#DistributedSystems #Raft #Consensus #Python #OpenSource #BuildInPublic
