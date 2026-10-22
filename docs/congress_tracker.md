# Congress tracker (estilo "Pelosi ETF")

Sistema de alertas y carteras réplica a partir de las operaciones bursátiles que
declaran los congresistas de EE. UU. (STOCK Act). Todo se ejecuta en GitHub
Actions y escribe en Google Sheets; las alertas llegan por Telegram.

## Flujo (`.github/workflows/congress_watch.yml`, cada 2 horas)

```text
House Clerk (índice + PDFs)  ─┐
Senate eFD (HTML)            ─┼─> collectors/collect_congress_ptr.py
Claude visión (escaneados)   ─┘     + comités (congress-legislators) + sector (lista curada / SIC SEC)
                                    + conflicto comité↔sector (config/conflict_sectors.json)
        -> data/congress_ptr_latest.csv -> raw_congress_ptr (Sheets)
        -> scripts/build_tracker.py   -> politician_scorecard, replica_holdings,
                                         copy_portfolio_summary / _nav / _positions
                                         (+ resumen semanal los lunes, con comentario de Claude)
        -> scripts/send_congress_alerts.py -> conflict_trades, ptr_manual_review,
                                         agent_review (investigación de Claude) y Telegram
```

El radar diario (`daily_radar.yml`) sigue usando `raw_congress_ptr` para cruzar con
contratos, insiders y catalizadores (`PTR_BUY_CONFLICT` en `signals`).

## Alertas

| Etiqueta | Cuándo |
|---|---|
| 🚨 CONFLICTO | Compra de un miembro de un comité con jurisdicción sobre el sector (score ≥ 50) |
| 👀 SEGUIDO | Cualquier compra o venta de un político de `config/tracker.json` |
| 🔎 VIGILADO | Compra de un político con ≥ 2 indicios de ventaja informativa (`insider_watchlist`) |
| 🧲 CLUSTER | 3+ congresistas compran el mismo ticker en 30 días |
| 📄 PAPEL | Un político seguido presenta una declaración escaneada que no se pudo leer |

Cada alerta incluye importe, titular, retraso de la declaración, movimiento del precio
desde la operación y desde la declaración, otros compradores, historial del político,
si la cartera réplica compra y, si hay API key, la evaluación del agente (sospecha 1-5,
tesis, catalizadores y fuentes). Deduplicadas en `telegram_alert_log`.

## Agente (Claude) — diseñado para coste mínimo

Opcional: requiere el secret `ANTHROPIC_API_KEY`. Sin él todo lo demás funciona.

* **Tope mensual duro** (`monthly_budget_usd`, por defecto 3 USD). Cada llamada estima su
  coste con precios conservadores y se apunta en la hoja `agent_usage`; al llegar al tope el
  agente se apaga hasta el mes siguiente. Pon además un límite en console.anthropic.com.
* **Investigación** (modelo barato `claude-haiku-5-5`, 2 búsquedas, máx. 3 tickers): solo
  compras en conflicto con score ≥ 60 o compras de políticos seguidos desde 15.001 USD;
  1 por ejecución y 3 al día. Resultado en `agent_review` y en la alerta.
* **Visión** (`claude-sonnet-5-5`): solo declaraciones escaneadas de políticos seguidos y de
  hasta 15 páginas (los extractos de broker largos quedan para revisión manual); máx. 2 por
  ejecución, cacheadas por DocID.
* **Comentario semanal**: desactivado por defecto (`weekly_memo`).
* Los cálculos numéricos nunca los hace el modelo.

Coste orientativo con estos valores: ~0,05 USD por investigación y ~0,10 USD por escaneo
corto; en uso normal 1–3 USD/mes y nunca más del tope configurado.

## Indicios de información privilegiada

* `insider_analysis`: compara grupos de compras con el S&P 500 a 30/90/180 días (con
  y sin relación con el comité, liderazgo, importe, declaración tardía, opciones).
  Con datos 2024–2026 las compras en sectores del propio comité ganan al índice
  (+2,3 % a 90 días) y el resto pierde (−0,6 %).
* `politician_scorecard` → banderas rojas (0–4) con el motivo: compras en sectores de su
  comité que baten al mercado y a sus demás compras, la acción sube antes de hacerse
  pública, declaraciones tardías frecuentes, ventaja fuerte a 30 días.
* `insider_watchlist`: políticos con ≥ 2 banderas. Sus compras disparan alertas 🔎.
* Indicios estadísticos sobre datos públicos, no pruebas.

## Carteras réplica

Definidas en `config/tracker.json -> portfolios`:

* `trumpito`: **compras en sectores que vigila el comité del congresista** (conflicto),
  la estrategia que mejor ha funcionado en la prueba histórica.
* `pelosi`: réplica de Nancy Pelosi. `congreso`: todo el Congreso, como referencia.

* Entrada al cierre del primer día hábil después de la fecha de declaración (sin mirar al futuro).
* Salida cuando el político declara la venta del mismo ticker o tras `max_hold_days`.
* Peso por importe declarado (punto medio del rango) con tope `max_lot_usd`.
* Índice diario ponderado en el tiempo (base 100) frente a SPY.
* Las opciones se tratan como exposición al subyacente.
* La regla `ranking_rule` ("bate al mercado") sigue disponible como `include: ["beaters"]`.

## Puesta en marcha

1. Secrets de GitHub: `GOOGLE_CREDENTIALS_JSON`, `GOOGLE_SHEETS_ID`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `SEC_USER_AGENT` y, opcional, `ANTHROPIC_API_KEY`.
2. Actions → Congress watch → Run workflow con `backfill_since = 2024-01-01`
   (`max_vision`, por defecto 5, limita cuántos escaneados lee Claude en la carga histórica).
3. A partir de ahí corre solo cada 2 horas; el propio workflow evita que GitHub lo desactive.

## Limitaciones conocidas

* La ley permite declarar hasta 45 días después: ninguna alerta puede ser en tiempo real.
* Senate eFD bloquea algunos rangos de IP fuera de EE. UU.; desde GitHub Actions debería funcionar.
* Precios de Yahoo Finance (no oficial): si falla un ticker se omite en esa ejecución.
* Simulación con datos públicos; no es una recomendación de inversión.
