"""Durable system of record for nodes and runs.

SQLite (on a mounted volume) for the local Docker topology; Firestore for Cloud Run, where the instance
filesystem is ephemeral. Select with MESH_LEDGER=sqlite|firestore.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time


class SqliteLedger:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, data TEXT, updated REAL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS runs (job_id TEXT, node_id TEXT, data TEXT, created REAL)")

    def upsert_node(self, node_id: str, record: dict) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO nodes VALUES (?, ?, ?)", (node_id, json.dumps(record), time.time()))

    def get_node(self, node_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_nodes(self) -> list[dict]:
        with self._lock:
            return [json.loads(r[0]) for r in self._db.execute("SELECT data FROM nodes ORDER BY updated DESC")]

    def add_run(self, run: dict) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO runs VALUES (?, ?, ?, ?)",
                             (run["job_id"], run["node_id"], json.dumps(run), time.time()))

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM runs ORDER BY created DESC LIMIT ?", (limit,))
            return [json.loads(r[0]) for r in rows]


class FirestoreLedger:
    def __init__(self):
        from google.cloud import firestore  # honours FIRESTORE_EMULATOR_HOST for local testing
        self._db = firestore.Client()

    def upsert_node(self, node_id: str, record: dict) -> None:
        self._db.collection("nodes").document(node_id).set({**record, "_updated": time.time()})

    def get_node(self, node_id: str) -> dict | None:
        snap = self._db.collection("nodes").document(node_id).get()
        return {k: v for k, v in snap.to_dict().items() if k != "_updated"} if snap.exists else None

    def list_nodes(self) -> list[dict]:
        return [{k: v for k, v in d.to_dict().items() if k != "_updated"}
                for d in self._db.collection("nodes").stream()]

    def add_run(self, run: dict) -> None:
        self._db.collection("runs").document(f"{run['job_id']}-{run['node_id']}").set({**run, "_created": time.time()})

    def list_runs(self, limit: int = 50) -> list[dict]:
        from google.cloud import firestore
        q = self._db.collection("runs").order_by("_created", direction=firestore.Query.DESCENDING).limit(limit)
        return [{k: v for k, v in d.to_dict().items() if k != "_created"} for d in q.stream()]


def make_ledger():
    if os.environ.get("MESH_LEDGER", "sqlite") == "firestore":
        return FirestoreLedger()
    return SqliteLedger(os.environ.get("MESH_DB", "mesh.db"))
