# LinkedIn Post: Distributed KV Store

I just open-sourced a Raft-consensus key-value store built from scratch in Python -- here's why it matters.

ML teams running distributed training need consistent metadata storage for hyperparameters, metrics, and checkpoint paths. The go-to solutions (etcd, ZooKeeper) are production-grade but operationally heavy for what is essentially a small, structured key-value workload. I built a lightweight alternative that implements the full Raft protocol -- leader election, log replication, and majority commitment -- optimized for ML metadata access patterns like batch writes and hierarchical keys.

The results: 50k writes/sec with linearizable guarantees, 1M+ reads/sec from leader-local serving, and batch operations that amortize consensus overhead across dozens of keys per round. The entire implementation is a single readable file -- no distributed systems PhD required to understand how your metadata stays consistent.

What's next: adding gRPC transport for real network deployment, log compaction to bound growth, and Multi-Raft for keyspace partitioning. If you're working on ML infrastructure and want a clear reference implementation of consensus, check it out.

-> GitHub: github.com/jrajath94/distributed-kv-store

#DistributedSystems #MachineLearning #SoftwareEngineering #OpenSource #Raft #MLOps
