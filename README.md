# ExperimentMesh

Agents that plan ML experiments and run them across teammates' machines, within each owner's limits,
then verify the results. CSCI 599 Assignment 4 capstone. See [PROPOSAL.md](PROPOSAL.md) for the full design.

**Status: week-1 foundation.** One job travels end-to-end:
1. the CLI sends it over A2A through the hub relay;
2. the node checks the owner's grant;
3. the job runs in the Docker sandbox, with metrics streaming back live;
4. a signed RunResult lands in the durable ledger.

The coordinator, LLM agents, verifier, approvals and evals come next.

## Layout
```
shared/   schemas.py (wire contracts) · signing.py (Ed25519 node identity)
node/     daemon.py · a2a_server.py · agent_card.py · policy/grants.py · executor/sandbox.py
hub/      app.py (registry, ledger API, A2A relay over WebSocket) · ledger.py (SQLite | Firestore)
cli/      mesh.py (nodes, runs, submit)
jobs/     smoke/ (stdlib test job) · fashion_mnist/ (PyTorch CPU) · Dockerfile.torch-cpu
deploy/   docker-compose.yml · Dockerfile.hub · node.env.example
tests/    test_core.py · test_sandbox.py · test_e2e.py
```

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```
Install Docker (Docker Desktop or colima) on every node so jobs run in the sandbox.
Without Docker, nodes refuse jobs unless `MESH_ALLOW_UNSANDBOXED=1` is set. That flag is dev only and gives no isolation.

## Run (one laptop)
```bash
# 1. hub (local topology: Docker + SQLite volume) — or without Docker:
#    MESH_DB=mesh.db .venv/bin/uvicorn hub.app:create_app --factory --port 8080
docker compose -f deploy/docker-compose.yml up -d
# 2. node (writes ~/.experimentmesh/{node_key.pem,grant.json} on first run; edit grant.json to set limits)
HUB_URL=http://127.0.0.1:8080 NODE_NAME=me .venv/bin/python -m node.daemon
# 3. client
.venv/bin/python -m cli.mesh nodes
.venv/bin/python -m cli.mesh submit jobs/smoke --params '{"lr": 0.3, "epochs": 5}'
.venv/bin/python -m cli.mesh runs
```
**Two laptops:** run the hub where both can reach it (Cloud Run, or one laptop on the same Wi-Fi), then point each node's `HUB_URL` at it.
Nodes only make outbound connections, so home routers need no port forwarding.

**PyTorch job:**
1. `docker build -f jobs/Dockerfile.torch-cpu -t experimentmesh/torch-cpu:0.1 jobs`
2. `mesh submit jobs/fashion_mnist --image experimentmesh/torch-cpu:0.1`

## Test
```bash
.venv/bin/pytest -q      # Docker-only sandbox tests skip when Docker is absent
```

## Deployment topology
**Hybrid.**
- Nodes run natively on each owner's machine, because they drive the local Docker and hardware.
- The hub runs either in Docker locally (SQLite on a named volume) or on Cloud Run with `MESH_LEDGER=firestore`.
- Cloud Run instances are ephemeral, so in the cloud Firestore is the system of record; nothing is persisted in-process.

## Capability-to-Code Map
Rows marked *planned* are design commitments, not yet implemented.

| Category | Claimed Feature / Guardrail / Eval | Source File(s) & Line Numbers | Key Class / Function | Notes / Scope |
|---|---|---|---|---|
| Advanced Feature 1 | Multi-agent A2A | `node/a2a_server.py` (L27-L91), `node/agent_card.py` (L41-L62), `hub/app.py` (L120-L148), `cli/mesh.py` (L44-L68) | `MeshNodeExecutor`, `build_agent_card`, `relay`, `submit` | A2A v1.0 JSON-RPC via `a2a-sdk` 1.3.1. Agent Card with a hardware-capabilities extension. Streaming task updates. The hub relays raw A2A bytes |
| Advanced Feature 2 | Multi-step planning + self-correction | *planned* `coordinator/graph.py` | — | Week 2–3 |
| Advanced Feature 3 | Human-in-the-loop approvals | *planned* `coordinator/`, `hub/telegram_bot.py` | — | Week 3 (LangGraph `interrupt()` ↔ A2A waiting-for-input state) |
| Advanced Feature 4 | Long-term memory | `hub/ledger.py` (L15-L75) | `SqliteLedger`, `FirestoreLedger` | Runs and nodes survive hub restart (verified). Learned priors: *planned* |
| Advanced Feature 5 | Hybrid cloud/local routing | *planned* `coordinator/router.py` | — | Week 4 |
| Guardrail 1 | Sandbox isolation | `node/executor/sandbox.py` (L66-L114) | `run_job`, `_run_docker` | Network off, read-only code, runs as `nobody`, Linux capabilities dropped, limits on processes, memory and CPU, timeout. Refuses to run without Docker |
| Guardrail 2 | Owner permission scoping | `node/policy/grants.py` (L10-L30), `node/a2a_server.py` (L54-L60) | `check_grant` | Image allowlist, time, CPU, memory, daily quota, allowed hours, expiry. Checked before the sandbox |
| Guardrail 3 | Input validation | `shared/schemas.py` (L40-L64) | `JobManifest` | Path traversal blocked, entrypoint must be included, code size capped |
| Guardrail 4 | Node identity and auth | `shared/signing.py`, `hub/app.py` (L45-L66, L89-L118) | `verify`, `register`, `tunnel_ws` | `node_id = sha256(pubkey)`. Signed registration and run reports with replay protection. Challenge-response on the tunnel |
| Adversarial Evals | ≥3 prompt-injection tests | *planned* `evals/adversarial/` | — | Week 5 |
| Evaluation Harness | Custom harness + baselines | *planned* `evals/harness.py`; foundation tests in `tests/` | — | Week 5 |
| Deployment / Persistence | Docker Compose hub + SQLite volume; Cloud Run + Firestore | `deploy/docker-compose.yml`, `deploy/Dockerfile.hub`, `hub/ledger.py` | `make_ledger` | Local volume and Firestore both survive restarts and redeploys |

## Team
- **Person 1:** coordinator, planner, verifier, evals.
- **Person 2:** node, sandbox, A2A relay, trust and approvals, deployment.

Each person writes their own `PROCESS_LOG_<name>.md` (200–300 words). The logs must disclose AI-assisted code.
