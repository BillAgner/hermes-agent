# commodity-inventory-monitor

Watch COMEX / warehouse metal inventory (registered + eligible split) and alert when the ratio drops below a configurable threshold. Currently supports silver and gold; copper/platinum/palladium extensible.

## Quick start

```bash
# Daily check, append to history
python scripts/fetch_comex_inventory.py --metal silver --threshold 0.25 \
    --state-file C:\Data\Hermes\cache\comex_inventory_history.jsonl

# See the last 30 days
python scripts/fetch_comex_inventory.py --metal silver --trend --days 30

# Validate the source is still parseable
python scripts/fetch_comex_inventory.py --metal silver --validate

# Force a test alert path
python scripts/fetch_comex_inventory.py --metal silver --threshold 0.99
```

## Wire into a cron job

See `templates/cron_prompt_addendum.md` for the drop-in prompt block.

## See also

- `SKILL.md` — full skill spec
- `references/data_sources.md` — what works, what's blocked
- `references/thresholds.md` — recommended thresholds per metal
