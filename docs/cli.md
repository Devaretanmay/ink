# CLI

```bash
microloop sites --db .microloop/decisions.db --json
microloop inspect refund.next_action --db .microloop/decisions.db --json
microloop compile refund.next_action --engine exact
microloop compile refund.next_action --engine laya --checkpoint /local/checkpoint --replace
microloop evaluate refund.next_action --verifier my_app:verify --requirements requirements.json
microloop maintenance
microloop maintenance --verifier my_app:verify --requirements requirements.json
microloop export /local/decision-history.json
microloop retain --before 1700000000
```

`sites` and `inspect` are read-only. Every command accepts `--db`; operation commands
return JSON. `--verifier` explicitly imports trusted application code. Requirements
are a JSON object matching `PromotionRequirements`; use experiment-specific values.
Without a verifier/profile, maintenance cannot promote candidates. Maintenance runs
once and exits; schedule it in your host application.

Retention removes old decisions only for sites without artifacts. Export preserves
all local audit tables in a versioned JSON document.

Legacy commands remain supported. Use `microloop inspect --legacy trajectory.jsonl`
for explicit trajectory inspection. Existing file-based inspection is recognized
for compatibility; `--db` explicitly selects decision-site inspection.
