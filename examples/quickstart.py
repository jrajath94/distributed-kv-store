"""Quick-start example for the distributed KV store.

Demonstrates creating a Raft cluster, electing a leader,
and performing key-value operations with consensus.
"""

from __future__ import annotations

import logging

from distributed_kv_store.core import RaftCluster
from distributed_kv_store.models import RaftConfig
from distributed_kv_store.utils import format_cluster_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Run the quick-start demo."""
    # ── Create a 5-node cluster ──────────────────────────────
    logger.info("Creating a 5-node Raft cluster...")
    config = RaftConfig(cluster_size=5)
    cluster = RaftCluster(config)

    # ── Elect a leader ───────────────────────────────────────
    leader_id = cluster.elect_leader()
    logger.info("Leader elected: %s", leader_id)

    # ── Store ML training metadata ───────────────────────────
    logger.info("Storing ML training metadata...")

    # Single writes (each goes through Raft consensus)
    cluster.put("experiment/name", "gpt-finetune-v3")
    cluster.put("experiment/model", "llama-7b")
    cluster.put("experiment/optimizer", "adamw")

    # Batch write (one consensus round for many keys)
    cluster.batch_put({
        "hyperparams/lr": "3e-4",
        "hyperparams/batch_size": "32",
        "hyperparams/warmup_steps": "500",
        "hyperparams/weight_decay": "0.01",
        "hyperparams/max_grad_norm": "1.0",
    })
    logger.info("Wrote 8 keys (3 individual + 5 batch)")

    # ── Read back values ─────────────────────────────────────
    logger.info("Reading values...")
    for key in ["experiment/name", "hyperparams/lr", "hyperparams/batch_size"]:
        value = cluster.get(key)
        logger.info("  %s = %s", key, value)

    # ── Update a value ───────────────────────────────────────
    cluster.put("hyperparams/lr", "1e-4")
    new_lr = cluster.get("hyperparams/lr")
    logger.info("Updated lr: %s", new_lr)

    # ── Delete a key ─────────────────────────────────────────
    cluster.delete("experiment/optimizer")
    deleted = cluster.get("experiment/optimizer")
    logger.info("Deleted optimizer key (now: %s)", deleted)

    # ── Store training metrics per epoch ─────────────────────
    logger.info("Storing epoch metrics...")
    for epoch in range(5):
        loss = 2.5 / (epoch + 1)
        acc = 0.6 + 0.08 * epoch
        cluster.batch_put({
            f"metrics/epoch_{epoch}/loss": f"{loss:.4f}",
            f"metrics/epoch_{epoch}/accuracy": f"{acc:.4f}",
        })

    # ── List all keys ────────────────────────────────────────
    all_keys = sorted(cluster.keys())
    logger.info("All keys (%d):", len(all_keys))
    for key in all_keys:
        logger.info("  %s = %s", key, cluster.get(key))

    # ── Cluster status ───────────────────────────────────────
    status = cluster.status()
    logger.info("Cluster status: %s", format_cluster_status(status))

    # ── Verify replication ───────────────────────────────────
    logger.info("Verifying replication across nodes...")
    leader = cluster.leader
    if leader:
        for node_id, node in cluster.nodes.items():
            logger.info(
                "  %s: log_length=%d, commit_index=%d, state=%s",
                node_id,
                len(node.log),
                node.commit_index,
                node.state.value,
            )

    logger.info("Quick-start complete.")


if __name__ == "__main__":
    main()
