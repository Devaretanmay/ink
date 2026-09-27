# Microloop

**Turn repeated agent decisions into verified fast paths.**

Microloop is a local decision JIT for AI agents. An agent starts with its original
model. Microloop records bounded decisions, builds a local candidate, runs it in
shadow, and promotes it only after independent outcome verification. Unfamiliar
states continue to use the original model.

```text
observe → candidate → shadow → verified → active
                        ↑                  │
                        └── outcome drift ─┘
```

## Development version: 0.4.0

Build this checkout with Python 3.10–3.13 and Rust:

```bash
uv venv --python 3.13
uv pip install maturin pytest ruff
maturin develop --manifest-path python/microloop/Cargo.toml
```

Laya-MLX is optional and currently requires Apple Silicon and Python 3.11+:

```bash
uv pip install 'laya-mlx==0.2.0'
```

Microloop never downloads checkpoints automatically. Supply a complete local
checkpoint to the Laya engine. The portable `exact` engine is a separate learned
frequency table, not Laya.

## Use inside an existing agent

```python
from microloop import DecisionSite, Microloop

site = DecisionSite(
    "refund.next_action",
    {"amount": "number", "payment_status": "string", "chargeback": "boolean"},
    ("refund", "request_information", "specialist"),
    fallback_revision="refund-prompt-v1",
)

with Microloop() as client:
    result = client.decide(
        site=site,
        state={"amount": 42.5, "payment_status": "settled", "chargeback": False},
        fallback=agent_decision,  # Your existing zero-argument model call.
    )
    receipt = execute_action(result.choice)  # Your application still owns execution.
    if result.recorded:
        client.record_outcome(
            result.decision_id,
            quality=receipt.quality,  # Bounded score in [0, 1].
            verifier="refund-ledger",
            verifier_version="1",
            evidence=receipt.evidence,
        )
```

`agent_decision` and `execute_action` above are application callbacks. For a runnable
example, see [the refund agent](examples/refund_agent/README.md).

Use `await client.decide_async(...)` for an async fallback. The top-level
`decision(...)` helper owns a default local client; explicit clients are preferred
for applications that manage lifecycle and storage paths.

## Qualification is explicit

Register sites, collect outcomes, then run maintenance in your application's job
schedule. There is no hidden worker. Compilation splits historical task groups
into training, calibration, and evaluation partitions. Calibration freezes an
experiment-specific profile. Promotion also requires fresh shadow tasks with
outcomes and an independent replay verifier.

The first release uses **exact typed-state coverage**. It does not claim to
recognize arbitrary similar cases. Raw model confidence cannot activate a path.
Sites without a reliable independent verifier remain fallback-driven.

```bash
microloop sites
microloop inspect refund.next_action --json
microloop compile refund.next_action --engine exact
microloop evaluate refund.next_action --verifier my_app:verify --requirements requirements.json
microloop maintenance
```

## Evidence and limitations

The included refund workload is generated, not customer production traffic.
Its default fallback is a labelled test fixture. A real model mode requires
`MICROLOOP_API_KEY` and an explicit `--model`; it records actual model usage.
Read [validation evidence](docs/validation-v0.4.md) before interpreting results.

Fallback invocations avoided, model calls, tokens, and outcome quality are separate
metrics. Microloop does not assume every fallback makes exactly one model call.

Everything stays local: `.microloop/decisions.db`, engine artifacts, and outcome
history. There is no cloud service, dashboard, workflow mining, or provider router.

## Compatibility and development

The v0.3 Rust core and Python imports remain available. Old Python implementation
lives under `microloop.internal`; [compatibility notes](docs/compatibility.md)
explain the boundary. The old episode database is unchanged.

- [Integration](docs/integration.md)
- [Architecture](docs/architecture.md)
- [CLI](docs/cli.md)
- [Concepts](docs/concepts.md)

Run `make check` for Rust/Python checks, existing examples, and wheel construction.
Laya and provider-backed experiments are opt-in and separate from offline CI.

Apache-2.0. Checkpoint licenses remain the responsibility of their distributors.
