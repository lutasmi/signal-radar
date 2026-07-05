Signal Radar - Docs Roadmap

The canonical roadmap is maintained in `ROADMAP.md`.

Current V1: the complete ingestion -> raw Google Sheets -> signals -> cluster_signals -> correlation_signals -> priority_signals -> review_queue -> Telegram alerts pipeline is consolidated and validated through `scripts/rebuild_radar.py` and `scripts/send_telegram_alerts.py`. Shared engine helpers live in `radar/`.

The next logical work is operational hardening inside the Google Sheets and Telegram product: observe scheduled runs, add validations for real failures, and simplify code where it improves reliability. Dashboard, backtesting, new sources, databases, and complex scoring require an explicit product decision.
