Signal Radar - Roadmap

Estado actual: V1 operativo como radar personal en Google Sheets con alertas Telegram. La ingesta, las raw sheets, `signals`, `cluster_signals`, `correlation_signals`, `priority_signals`, `review_queue`, scoring configurable y `telegram_alert_log` forman el producto actual.

Fuente de verdad: Google Sheets.

Punto único de ejecución: scripts/rebuild_radar.py.

Motor interno compartido: radar/.

Fase 1
✔ Ingesta Capitol Trades

Fase 2
✔ Ingesta SEC Form 4

Fase 3
✔ Ingesta USASpending

Fase 4
✔ signals baseline

Estado:

* scripts/build_signals_sheet.py existe.
* La pestaña signals existe.
* signal_date normalization está implementado.
* Existe validación offline de CSV, idempotencia de loaders y generación determinística de signals.
* El flujo collectors → CSV → raw sheets → signals fue validado contra Google Sheets real el 2026-06-28.
* CI mínimo activo para validación local con fixtures.
* Automatización diaria controlada activa en GitHub Actions.

V1
✔ Radar diario en Google Sheets

Objetivo:

* Detectar actividad repetida por ticker.
* Detectar contratos repetidos por entidad.
* Generar alertas explicables, auditables e idempotentes en cluster_signals.
* Conectar clusters con señales de mercado relacionadas en correlation_signals.
* Ordenar oportunidades con prioridad HIGH/MEDIUM/LOW y explicación auditable.
* Entregar oportunidades a una cola interna de revisión humana dentro de Google Sheets.
* Puntuar oportunidades con pesos configurables en `config/scoring.json`.
* Enviar alertas Telegram deduplicadas para oportunidades relevantes.
* Mostrar qué oportunidades son NEW, ACTIVE, CLOSED y cuáles merecen revisión hoy.
* Mantener una arquitectura interna reusable con collectors, loaders, transformaciones, validaciones y pipeline separados.

Fase 5
✔ clusters

Fase 6
✔ correlaciones

Fase 7
✔ scoring simple

Fase 8
✔ alertas internas y Telegram

Fase 9
✔ seguimiento diario en review_queue

Siguientes mejoras permitidas dentro de V1
□ Observar ejecuciones reales del workflow diario y corregir fallos operativos.
□ Endurecer validaciones locales cuando aparezcan nuevos casos reales.
□ Simplificar duplicación interna sin cambiar la arquitectura.

Fuera de alcance salvo decisión explícita
□ dashboard
□ backtesting
□ nuevas fuentes
