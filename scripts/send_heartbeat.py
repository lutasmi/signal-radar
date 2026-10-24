"""Daily "everything is working" message for Telegram.

Sent once a day so silence never means "something broke": it reports the
health of the last 24 h of runs, how many new disclosures arrived, how many
alerts were sent and how the main replica portfolio is doing. If anything
failed it says so in plain words.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from radar.runtime import ensure_project_runtime

ensure_project_runtime(["gspread", "dotenv", "google.oauth2.service_account", "requests"])

STATUS_FILE = PROJECT_ROOT / "data" / "reference" / "collect_status.json"
WORKFLOWS = {"congress_watch.yml": "vigilancia del Congreso", "daily_radar.yml": "radar diario"}
MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
STEP_NAMES = {
    "STEP_COLLECT": "descargar las declaraciones",
    "STEP_LOAD": "guardar en Google Sheets",
    "STEP_TRACKER": "calcular carteras y ranking",
    "STEP_ALERTS": "enviar alertas",
}


def pct_es(value):
    try:
        return f"{float(str(value).replace(',', '.')):+.1f} %".replace(".", ",").replace("-", "−")
    except ValueError:
        return "n/d"


def recent_runs(repo, token, since):
    """{workflow: (ok, failed)} for scheduled/manual runs created since `since` (excluding this run)."""
    import requests

    result = {}
    this_run = os.getenv("GITHUB_RUN_ID", "")
    for workflow in WORKFLOWS:
        response = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs",
            params={"created": f">={since.isoformat()}", "per_page": 100},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        response.raise_for_status()
        runs = [r for r in response.json().get("workflow_runs", []) if str(r["id"]) != this_run and r["status"] == "completed"]
        ok = sum(1 for r in runs if r["conclusion"] == "success")
        result[workflow] = (ok, len(runs) - ok)
    return result


def build_message(today, step_outcomes, status, runs, new_filings, alerts_sent, summary):
    problems = []
    for env_name, label in STEP_NAMES.items():
        outcome = step_outcomes.get(env_name, "")
        if outcome and outcome not in ("success", "skipped"):
            problems.append(f"Hoy ha fallado el paso de {label}.")
    if status:
        for chamber, label in (("house", "la Cámara de Representantes"), ("senate", "el Senado")):
            info = status.get(chamber, {})
            if info.get("checked") and not info.get("ok"):
                problems.append(f"No se han podido descargar las declaraciones de {label}.")
    else:
        problems.append("No hay registro de la última descarga de declaraciones.")
    if runs is None:
        problems.append("No he podido comprobar las ejecuciones de las últimas 24 h.")
    else:
        for workflow, (ok, failed) in runs.items():
            if failed:
                problems.append(f"El proceso de {WORKFLOWS[workflow]} ha fallado {failed} de {ok + failed} veces en 24 h.")
        if runs.get("congress_watch.yml", (0, 0)) == (0, 0):
            problems.append("La vigilancia del Congreso no se ha ejecutado en las últimas 24 h (aparte de ahora).")

    day = f"{today.day} {MONTHS_ES[today.month - 1]}"
    head = "✅ Trumpito · revisión diaria" if not problems else "⚠️ Trumpito · revisión diaria: hay algo que revisar"
    lines = [f"{head} ({day})", ""]
    lines.append("Todo funciona correctamente." if not problems else "Problemas detectados:")
    lines += [f"• {p}" for p in problems]
    lines.append("")
    if runs is not None:
        total_ok = sum(ok for ok, _ in runs.values())
        total = sum(ok + failed for ok, failed in runs.values())
        lines.append(f"• Ejecuciones en las últimas 24 h: {total_ok} de {total} correctas.")
    house, senate = new_filings
    lines.append(f"• Declaraciones nuevas publicadas (ayer y hoy): {house} de la Cámara y {senate} del Senado.")
    if alerts_sent:
        lines.append(f"• Alertas enviadas en las últimas 24 h: {alerts_sent}. Revísalas más arriba en este chat.")
    else:
        lines.append("• Alertas en las últimas 24 h: ninguna. Hoy no hay nada que requiera tu atención.")
    if summary:
        lines.append(
            f"• {summary['name'].split(' (')[0]}: {pct_es(summary['total_return_pct'])} desde {summary['start']} "
            f"(S&P 500 {pct_es(summary['benchmark_return_pct'])})."
        )
    lines += ["", "Si un día no recibes este mensaje, algo ha fallado: avísame."]
    return "\n".join(lines)


def collect_facts(sheet, today):
    from radar.sheets import read_records_or_empty

    yesterday = (today - timedelta(days=1)).isoformat()
    filings = {"house": set(), "senate": set()}
    for row in read_records_or_empty(sheet, "raw_congress_ptr"):
        if (row.get("filing_date") or "") >= yesterday and row.get("chamber") in filings:
            filings[row["chamber"]].add(row.get("filing_id"))
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    alerts = {
        row.get("item_id", "").split(":")[0]
        for row in read_records_or_empty(sheet, "telegram_alert_log")
        if (row.get("sent_at") or "") >= since[:19]
        and (row.get("score_band") or "").startswith(("SENT", "SUMMARIZED"))
    }
    summaries = read_records_or_empty(sheet, "copy_portfolio_summary")
    return (len(filings["house"]), len(filings["senate"])), len(alerts), (summaries[0] if summaries else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from dotenv import load_dotenv

    from radar.sheets import open_sheet
    from scripts import send_telegram_alerts as telegram

    load_dotenv()
    today = date.today()
    status = json.loads(STATUS_FILE.read_text(encoding="utf-8")) if STATUS_FILE.exists() else None
    steps = {name: os.getenv(name, "") for name in STEP_NAMES}
    try:
        runs = recent_runs(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"], datetime.now(timezone.utc) - timedelta(hours=24))
    except Exception as exc:
        print(f"WARNING: could not read workflow runs: {exc}")
        runs = None
    try:
        new_filings, alerts_sent, summary = collect_facts(open_sheet(), today)
    except Exception as exc:
        print(f"WARNING: could not read Google Sheets: {exc}")
        new_filings, alerts_sent, summary = (0, 0), 0, None
        steps["STEP_LOAD"] = steps.get("STEP_LOAD") or "failure"
    message = build_message(today, steps, status, runs, new_filings, alerts_sent, summary)
    print(message)
    if not args.dry_run:
        token, chat_id = telegram.telegram_credentials()
        telegram.send_telegram_message(token, chat_id, message)
        print("Heartbeat sent")


def validate_logic():
    ok = build_message(
        date(2026, 10, 24), {"STEP_COLLECT": "success"},
        {"house": {"checked": True, "ok": True}, "senate": {"checked": True, "ok": True}},
        {"congress_watch.yml": (5, 0), "daily_radar.yml": (1, 0)}, (3, 1), 0,
        {"name": "Trumpito ETF (x)", "total_return_pct": "105,5", "benchmark_return_pct": "66.2", "start": "2024-01-08"},
    )
    for expected in ["✅", "Todo funciona correctamente", "6 de 6 correctas", "3 de la Cámara y 1 del Senado",
                     "no hay nada que requiera tu atención", "Trumpito ETF: +105,5 %"]:
        if expected not in ok:
            raise ValueError(f"heartbeat OK message missing {expected!r}:\n{ok}")
    bad = build_message(
        date(2026, 10, 24), {"STEP_COLLECT": "failure"},
        {"house": {"checked": True, "ok": True}, "senate": {"checked": True, "ok": False}},
        {"congress_watch.yml": (0, 0), "daily_radar.yml": (0, 1)}, (0, 0), 2, None,
    )
    for expected in ["⚠️", "descargar las declaraciones", "el Senado", "radar diario ha fallado 1 de 1",
                     "no se ha ejecutado", "Alertas enviadas en las últimas 24 h: 2"]:
        if expected not in bad:
            raise ValueError(f"heartbeat problem message missing {expected!r}:\n{bad}")
    return True


if __name__ == "__main__":
    if "--validate" in sys.argv:
        validate_logic()
        print("OK heartbeat logic")
    else:
        main()
