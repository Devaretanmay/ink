# CLI

```bash
microloop sites --db .microloop/decisions.db --json
microloop inspect refund.next_action --db .microloop/decisions.db --json
microloop model-install
microloop compile refund.next_action
microloop compile refund.next_action --checkpoint /local/checkpoint --replace
microloop evaluate refund.next_action --verifier my_app:verify --requirements requirements.json
microloop maintenance
microloop maintenance --verifier my_app:verify --requirements requirements.json
microloop export /local/decision-history.json  # positional 'site' is the output path
microloop retain --before 1700000000
microloop retain refund.next_action --before 1700000000  # per-site retention
```

`sites` and `inspect` are read-only and print human tables unless `--json`.
`compile`, `evaluate`, `maintenance`, `export`, and `retain` always print JSON.
Every command accepts `--db`; `--verifier` explicitly imports trusted application code. Requirements
are a JSON object matching `PromotionRequirements`; use experiment-specific values.
Without a verifier/profile, maintenance cannot promote candidates. Maintenance runs
once and exits; schedule it in your host application.

Retention removes old decisions only for sites without artifacts. Export preserves
all local audit tables in a versioned JSON document.

Legacy commands remain supported. Use `microloop inspect --legacy trajectory.jsonl`
for explicit trajectory inspection. Existing file-based inspection is recognized
for compatibility; `--db` explicitly selects decision-site inspection.
