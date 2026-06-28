Signal Radar - Docs Roadmap

The canonical roadmap is maintained in `ROADMAP.md`.

Current baseline: the complete ingestion -> raw Google Sheets -> signals -> cluster_signals -> correlation_signals -> priority_signals -> review_queue pipeline is consolidated and validated through `scripts/rebuild_radar.py`. Shared engine helpers live in `radar/`. The next product phase is either dashboard or external alert delivery; Telegram and new channels require an explicit product decision.
