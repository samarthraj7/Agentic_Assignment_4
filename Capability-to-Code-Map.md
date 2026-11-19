# Capability-to-Code Map

| Category | Claimed Feature / Guardrail / Eval | Source File(s) & Line Numbers | Key Class / Function | Notes / Scope |
|---|---|---|---|---|
| Advanced Feature 1 | e.g. Long-term memory | src/memory/store.py (L45-L92) | DurableFirestoreStore | Survives container restarts |
| Advanced Feature 2 | e.g. Multi-agent A2A | src/agents/protocol.py (L12-L60) | A2AHandler | Implements A2A v1.0 spec |
| Advanced Feature 3 | e.g. HITL Approvals | src/workflows/review.py (L80-L115) | review_interrupt() | LangGraph interrupt surface |
| Guardrail 1 | e.g. Permission scoping | src/security/rbac.py (L20-L45) | enforce_scope() | Enforceable code-level check |
| Adversarial Evals | 3 Prompt Injection tests | evals/adversarial_logs.json | run_eval_suite() | Traces & mitigations documented |
| Evaluation Harness | e.g. Custom task-success harness / LangSmith | evals/harness.py (L10-L80) | run_benchmark() | Metrics: success rate, latency, tokens per session |
| Deployment / Persistence | e.g. Cloud Run + Firestore | Dockerfile, deploy/cloudrun.yaml, src/memory/store.py (L45-L92) | DurableFirestoreStore | State survives restarts and redeploys |
