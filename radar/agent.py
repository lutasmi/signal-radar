"""Claude-powered helpers: scanned PTR extraction, trade investigation, weekly memo.

Uses the Anthropic Messages API over plain HTTP (requests) to keep dependencies
minimal. Every function degrades gracefully: without ANTHROPIC_API_KEY the
pipeline keeps working with deterministic rules only.

Cost controls: results are cached (data/reference/agent/), runs are capped by
config/tracker.json -> "agent", and usage is accumulated in AgentClient.usage.
"""

import base64
import json
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "data" / "reference" / "agent"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

TRADE_TYPES = ["buy", "sell", "sell_partial", "exchange", "other"]

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_periodic_transaction_report": {"type": "boolean"},
        "filer_name": {"type": "string"},
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "SP, JT, DC or Self"},
                    "asset_name": {"type": "string"},
                    "ticker": {"type": "string", "description": "Ticker symbol or empty string"},
                    "asset_type": {"type": "string", "description": "ST stock, OP option, EF ETF, GS government security, CS corporate bond, other code or empty"},
                    "trade_type": {"type": "string", "enum": TRADE_TYPES},
                    "transaction_date": {"type": "string", "description": "YYYY-MM-DD or empty"},
                    "notification_date": {"type": "string", "description": "YYYY-MM-DD or empty"},
                    "amount": {"type": "string", "description": "Range exactly as reported, e.g. $1,001 - $15,000"},
                },
                "required": [
                    "owner", "asset_name", "ticker", "asset_type", "trade_type",
                    "transaction_date", "notification_date", "amount",
                ],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["is_periodic_transaction_report", "filer_name", "transactions", "notes"],
    "additionalProperties": False,
}

REVIEW_TOOL = {
    "name": "record_review",
    "description": "Record the final assessment for each traded ticker. Call exactly once when research is done.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "assessments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "suspicion": {
                            "type": "integer",
                            "enum": [1, 2, 3, 4, 5],
                            "description": "1 = routine/no informational edge visible, 5 = strong signs of non-public information",
                        },
                        "thesis": {"type": "string", "description": "Two sentences in Spanish: why this trade matters (or not)."},
                        "evidence": {"type": "string", "description": "Concrete facts with dates, in Spanish, separated by ' | '."},
                        "catalysts": {"type": "string", "description": "Upcoming events that could move the stock, in Spanish, or empty."},
                        "sources": {"type": "array", "items": {"type": "string"}, "description": "URLs actually consulted."},
                    },
                    "required": ["ticker", "suspicion", "thesis", "evidence", "catalysts", "sources"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["assessments"],
        "additionalProperties": False,
    },
}

REVIEW_SYSTEM = """Eres un analista que investiga operaciones bursátiles de congresistas de EE. UU.
para detectar posible uso de información privilegiada (STOCK Act). Recibes operaciones ya
declaradas oficialmente y su contexto. Para cada ticker:
1. Busca en la web hechos verificables en la ventana de ~90 días antes y ~60 días después de la
   operación: contratos federales (USASpending, DoD), audiencias o votaciones del comité del
   congresista, legislación o regulación que afecte a la empresa, resultados y noticias relevantes.
2. Distingue correlación de evidencia: compras pequeñas, de fondos indexados o gestionadas por
   asesores suelen ser rutinarias.
3. Puntúa la sospecha de 1 a 5 y explica por qué en español, sin inventar datos: si no encuentras
   evidencia, dilo.
Sé eficiente: pocas búsquedas bien dirigidas. Al terminar llama a record_review una única vez."""

MEMO_SYSTEM = """Eres un analista que escribe un comentario semanal breve (5-8 líneas, español,
tono sobrio) sobre carteras simuladas que copian operaciones declaradas por congresistas de EE. UU.
Usa SOLO las cifras proporcionadas; no inventes datos ni des recomendaciones de inversión."""


def api_key():
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


class AgentClient:
    def __init__(self, session, config, key=None):
        self.session = session
        self.config = config
        self.key = key if key is not None else api_key()
        self.usage = {"input_tokens": 0, "output_tokens": 0, "web_search_requests": 0, "calls": 0}

    @property
    def enabled(self):
        return bool(self.key) and self.config.get("enabled", True)

    # transport ------------------------------------------------------------
    def _post(self, payload, timeout=600):
        headers = {
            "x-api-key": self.key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        last = None
        for attempt in range(4):
            response = self.session.post(API_URL, headers=headers, json=payload, timeout=timeout)
            if response.status_code in (429, 500, 502, 503, 529):
                last = f"HTTP {response.status_code}: {response.text[:300]}"
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Anthropic API error {response.status_code}: {response.text[:500]}")
            data = response.json()
            usage = data.get("usage", {})
            self.usage["calls"] += 1
            self.usage["input_tokens"] += usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
            self.usage["output_tokens"] += usage.get("output_tokens", 0)
            self.usage["web_search_requests"] += (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
            return data
        raise RuntimeError(f"Anthropic API unavailable: {last}")

    def usage_line(self):
        u = self.usage
        return (
            f"Claude usage: {u['calls']} calls, {u['input_tokens']} input tokens, "
            f"{u['output_tokens']} output tokens, {u['web_search_requests']} web searches"
        )

    # scanned PTR extraction ----------------------------------------------
    def extract_scanned_ptr(self, pdf_bytes, filer_name, doc_id, cache_dir=CACHE_DIR):
        """Read a scanned House PTR with vision. Cached by doc_id (never billed twice)."""
        cache = Path(cache_dir) / "vision" / f"{doc_id}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        payload = {
            "model": self.config.get("vision_model", "claude-sonnet-5-5"),
            "max_tokens": 32000,
            "output_config": {"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Documento escaneado de la Cámara de Representantes de EE. UU. presentado por {filer_name}. "
                                "Si es un Periodic Transaction Report (puede incluir extractos de broker adjuntos), extrae "
                                "TODAS las transacciones declaradas: titular (SP/JT/DC o Self), activo, ticker (si aparece o "
                                "es inequívoco), tipo de activo, tipo de operación (P=buy, S=sell, S (partial)=sell_partial, "
                                "E=exchange), fechas en formato YYYY-MM-DD e importe tal como figura. No inventes filas; "
                                "si un campo es ilegible déjalo vacío y explícalo en notes."
                            ),
                        },
                    ],
                }
            ],
        }
        data = self._post(payload)
        text = next((b["text"] for b in data.get("content", []) if b.get("type") == "text"), "")
        if data.get("stop_reason") not in ("end_turn", "stop_sequence") or not text:
            raise RuntimeError(f"vision extraction incomplete for {doc_id}: {data.get('stop_reason')}")
        result = json.loads(text)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return result

    # trade investigation --------------------------------------------------
    def review_trades(self, context_text, tickers, max_searches=6, max_turns=8):
        """Research a disclosure with web search and return per-ticker assessments."""
        messages = [{"role": "user", "content": context_text}]
        tools = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches},
            REVIEW_TOOL,
        ]
        for _ in range(max_turns):
            data = self._post(
                {
                    "model": self.config.get("model", "claude-sonnet-5-5"),
                    "max_tokens": 8000,
                    "system": REVIEW_SYSTEM,
                    "tools": tools,
                    "messages": messages,
                }
            )
            content = data.get("content", [])
            for block in content:
                if block.get("type") == "tool_use" and block.get("name") == "record_review":
                    return block["input"].get("assessments", [])
            messages.append({"role": "assistant", "content": content})
            if data.get("stop_reason") == "pause_turn":
                continue  # long search turn: send it back unchanged to resume
            messages.append(
                {
                    "role": "user",
                    "content": "Termina ahora: llama a record_review con tu evaluación de "
                    + ", ".join(tickers) + ".",
                }
            )
        raise RuntimeError("review did not finish")

    # weekly memo ----------------------------------------------------------
    def weekly_memo(self, facts_text):
        data = self._post(
            {
                "model": self.config.get("model", "claude-sonnet-5-5"),
                "max_tokens": 1200,
                "system": MEMO_SYSTEM,
                "messages": [{"role": "user", "content": facts_text}],
            }
        )
        return "\n".join(b["text"] for b in data.get("content", []) if b.get("type") == "text").strip()
