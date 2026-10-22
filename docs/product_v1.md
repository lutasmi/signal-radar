# Signal Radar V1 - Producto Construido

Este documento describe el producto Signal Radar en su version V1: que hace,
que fuentes usa, como se opera y cuales son sus limites.

Signal Radar V1 es un radar diario de senales publicas. Su objetivo es ayudar a
detectar oportunidades de revision cuando distintas fuentes oficiales o publicas
apuntan a actividad relevante sobre empresas, contratos, legislacion,
regulacion, lobby, financiacion publica o eventos corporativos.

El producto no ejecuta operaciones, no recomienda comprar o vender, no hace
scoring predictivo y no sustituye la revision humana. Ordena trabajo de
investigacion.

## Resumen ejecutivo

Signal Radar V1 ya entrega un flujo operativo completo:

```text
fuentes publicas/oficiales
-> CSV y raw JSON/XML auditables
-> raw Google Sheets
-> signals
-> cluster_signals
-> correlation_signals
-> priority_signals
-> review_queue
-> Telegram
```

Google Sheets es la fuente de verdad del producto.

Telegram es el canal de alerta operativo.

Los CSV y respuestas raw locales son artefactos de extraccion y auditoria, no
son la fuente historica principal.

## Problema que resuelve

Las fuentes publicas relevantes para mercado, politica y contratacion estan
fragmentadas. Revisarlas manualmente cada dia es lento y propenso a perder
coincidencias.

Signal Radar V1 resuelve esto en cuatro pasos:

1. Extrae datos reales de fuentes publicas/oficiales.
2. Normaliza cada registro a senales comparables.
3. Agrupa y prioriza coincidencias explicables.
4. Entrega una cola diaria de revision y alertas Telegram deduplicadas.

El resultado no es una conclusion automatica, sino una lista corta de cosas que
merecen mirar.

## Usuario objetivo

El usuario principal es una persona que quiere revisar por la manana:

* que operaciones politicas o insiders aparecieron;
* que contratos, grants o avisos oficiales se movieron;
* que actividad legislativa, regulatoria o de lobby puede actuar como catalizador;
* que coincidencias merecen investigacion humana;
* que oportunidades son nuevas, siguen activas o se cerraron.

El flujo esta pensado para uso individual o de equipo pequeno, sin infraestructura
pesada.

## Principios del producto

V1 se mantiene deliberadamente simple:

* Google Sheets como fuente de verdad.
* Telegram como canal de alerta.
* Colectores independientes por fuente.
* Loaders idempotentes hacia raw tabs.
* Transformaciones deterministicas.
* Score configurable, no predictivo.
* Sin base de datos propia.
* Sin dashboard.
* Sin microservicios.
* Sin colas.
* Sin modelos de inteligencia artificial.
* Sin ejecucion automatica de decisiones financieras.

## Fuentes integradas

### Fuentes base

| Fuente | Raw tab | Senales principales |
| --- | --- | --- |
| Capitol Trades | `raw_capitol_trades` | `PTR_BUY`, `PTR_SELL` |
| SEC Form 4 | `raw_sec_form4` | `INSIDER_BUY` |
| USASpending | `raw_usaspending` | `CONTRACT` |

### Fuentes oficiales V1 ampliadas

| Fuente | Raw tab | Estado | Senales principales |
| --- | --- | --- | --- |
| SAM.gov | `raw_sam_gov` | validada e integrada | `CONTRACT_OPPORTUNITY`, `CONTRACT_PRE_SOLICITATION`, `CONTRACT_SOLE_SOURCE`, `CONTRACT_AWARD_NOTICE` |
| Congress.gov | `raw_congress_gov` | validada e integrada | `BILL_INTRODUCED`, `COMMITTEE_ACTION`, `LEGISLATIVE_ACTION` |
| Federal Register | `raw_federal_register` | validada e integrada | `RULE_PROPOSED`, `RULE_FINAL`, `AGENCY_NOTICE`, `EXECUTIVE_ACTION`, `REGULATORY_CATALYST` |
| LDA.gov | `raw_lda_gov` | validada e integrada | `LOBBY_NEW_REGISTRATION`, `LOBBY_SPEND_INCREASE`, `LOBBY_ISSUE_MATCH`, `LOBBY_CLIENT_ACTIVITY` |
| SEC EDGAR adicional | `raw_sec_edgar_additional` | validada e integrada | `MATERIAL_EVENT_8K`, `MAJOR_HOLDER_ENTRY`, `FORM144_PLANNED_SALE`, `INSTITUTIONAL_POSITION_13F` |
| FEC | `raw_fec` | validada e integrada | `POLITICAL_CONTRIBUTION`, `PAC_CONTRIBUTION`, `EXECUTIVE_CONTRIBUTION` |
| Grants.gov | `raw_grants_gov` | validada e integrada | `GRANT_FORECAST`, `GRANT_OPPORTUNITY` |

### Fuente preparada pero no integrada en vivo

| Fuente | Estado | Motivo |
| --- | --- | --- |
| USPTO | bloqueada por credenciales | requiere `USPTO_API_KEY` para validacion viva oficial |

USPTO no se declara validada en vivo y no forma parte del flujo operativo diario
hasta disponer de credenciales oficiales y datos reales verificados.

## Modelo de datos en Google Sheets

### Raw tabs

Cada fuente mantiene su propia pestana raw.

Las raw tabs conservan la estructura original normalizada minima de cada fuente.
No mezclan esquemas incompatibles.

Objetivo:

* mantener trazabilidad;
* evitar perdida de datos fuente;
* permitir reconstruir las hojas derivadas;
* preservar la historia en Google Sheets.

### `signals`

`signals` es la capa comun.

Cada fila representa una senal normalizada con campos como:

* `signal_id`;
* `signal_date`;
* `signal_type`;
* `ticker`;
* `entity_name`;
* `actor_name`;
* `actor_type`;
* `amount`;
* `source`;
* `source_url`;
* `raw_key`.

Esta capa permite comparar datos que vienen de fuentes distintas sin perder el
origen.

### `cluster_signals`

Agrupa senales relacionadas.

Tipos actuales:

* `TICKER_ACTIVITY`: actividad repetida sobre un ticker.
* `CROSS_SOURCE`: coincidencia de varias fuentes sobre un mismo ticker.
* `REPEATED_CONTRACTS`: contratos repetidos para una misma entidad.
* `PUBLIC_CATALYST_ACTIVITY`: actividad publica oficial repetida para una misma
  entidad o asunto.

Cada cluster conserva:

* fechas primera y ultima;
* fuentes;
* actores;
* razon explicativa;
* ids de senales fuente.

### `correlation_signals`

Detecta correlaciones simples y auditables entre clusters y senales.

Ejemplos:

* actividad contractual relacionada con una senal de mercado;
* cluster de ticker con senales cercanas adicionales.

No hace entity resolution avanzado ni correspondencias inventadas.

### `priority_signals`

Convierte clusters y correlaciones en oportunidades priorizadas.

Campos relevantes:

* `priority_level`: `HIGH`, `MEDIUM`, `LOW`;
* `opportunity_type`;
* `ticker`;
* `entity_name`;
* `sources`;
* `evidence_count`;
* `reason`;
* `priority_reason`;
* `cluster_ids`;
* `signal_ids`.

La prioridad es explicable, no opaca.

### `review_queue`

Es la cola operativa diaria.

Campos principales:

* `review_id`;
* `status`: `NEW`, `ACTIVE`, `CLOSED`;
* `review_today`: `YES` o `NO`;
* `first_seen`;
* `last_seen`;
* `closed_date`;
* `review_status`;
* `review_note`;
* `score`;
* `score_band`;
* `score_reason`;
* `summary`;
* `priority_reason`;
* `signal_ids`.

`review_status` y `review_note` son campos manuales y se preservan entre
reconstrucciones.

### `telegram_alert_log`

Registra alertas ya enviadas.

Evita que una misma oportunidad se envie repetidamente si el workflow se lanza
mas de una vez.

## Ciclo de vida de oportunidades

Una oportunidad puede estar en tres estados:

| Estado | Significado |
| --- | --- |
| `NEW` | aparece por primera vez en la cola o sigue siendo nueva durante el dia |
| `ACTIVE` | ya existia y sigue apareciendo en prioridades |
| `CLOSED` | dejo de aparecer en las prioridades actuales, pero se conserva por historial |

`review_today` se marca como `YES` cuando:

* la oportunidad es nueva; o
* la oportunidad activa es de prioridad HIGH.

Las oportunidades cerradas no generan alertas Telegram.

## Scoring

El scoring esta documentado en detalle en `docs/scoring.md`.

Resumen:

```text
score =
  prioridad base
+ estado
+ review_today
+ diversidad de fuentes
+ tipo de oportunidad
+ tipos de senal vinculados
+ recencia
```

Bandas actuales:

| Banda | Condicion |
| --- | --- |
| HIGH | score >= 85 |
| MEDIUM | score >= 45 |
| LOW | score >= 0 |

El score se guarda con una explicacion auditable en `score_reason`.

Los pesos viven en:

```text
config/scoring.json
```

Cambiar pesos no requiere editar Python.

## Telegram

Telegram es el canal operativo V1.

Una alerta se envia si:

* `status = NEW`; o
* `review_today = YES`; o
* `score_band = HIGH`.

Excepcion: `status = CLOSED` nunca genera alerta.

Cada mensaje incluye:

* asunto;
* quien compra o actua;
* que compra, recibe o queda afectado;
* vinculacion o relevancia;
* detalle de senales vinculadas;
* score total;
* explicacion clara del scoring;
* estado;
* instruccion de revisar Google Sheets.

Ejemplo de estructura:

```text
Signal Radar - HIGH

Asunto: TSM
Quien compra / actua: ...
Que compra / recibe / queda afectado: ...

Vinculacion / relevancia:
- Multiple source families reference the same ticker.
- Tipo de vinculo: CROSS_SOURCE
- Evidencia: 4 elementos de capitol_trades; sec_form4

Detalle:
- 2026-06-30 | ... compra TSM ...
- 2026-06-30 | ... compra TSM ...

Scoring:
- Total: 126 (HIGH)
- Metodo: suma configurable de prioridad, estado, diversidad de fuentes,
  tipo de oportunidad, tipos de senal y recencia.
- Prioridad HIGH: +50
- Estado ACTIVE: +5
- Revisar hoy: +15

Estado: ACTIVE
Revision: abrir Google Sheets
```

## Operacion diaria

El workflow diario esta en:

```text
.github/workflows/daily_radar.yml
```

Hace:

1. Ejecuta collectors.
2. Genera CSV locales.
3. Carga CSV en raw Google Sheets con loaders idempotentes.
4. Valida calidad de raw tabs.
5. Reconstruye hojas derivadas.
6. Envia resumen de captura.
7. Envia alertas Telegram deduplicadas.

Horario programado:

```text
07:15 UTC
```

Durante horario de verano en Madrid equivale a:

```text
09:15 Europe/Madrid
```

## Comandos operativos

Validacion local sin credenciales:

```bash
python3 scripts/validate_all.py
```

Validacion local usando CSV generados:

```bash
python3 scripts/validate_all.py --generated-csv
```

Reconstruccion completa contra Google Sheets:

```bash
python3 scripts/rebuild_radar.py
```

Validacion de calidad raw contra Google Sheets:

```bash
python3 scripts/validate_raw_quality.py
```

Metricas operativas:

```bash
python3 scripts/collect_daily_metrics.py
```

Prueba de mensajes Telegram sin enviar:

```bash
python3 scripts/send_telegram_alerts.py --dry-run
```

Test tecnico de Telegram:

```bash
python3 scripts/send_telegram_alerts.py --test
```

Envio real de alertas:

```bash
python3 scripts/send_telegram_alerts.py
```

## Configuracion necesaria

Variables y secretos:

| Variable | Uso |
| --- | --- |
| `GOOGLE_SHEETS_ID` | spreadsheet principal |
| `GOOGLE_CREDENTIALS_JSON` | secreto GitHub con service account JSON |
| `TELEGRAM_BOT_TOKEN` | bot Telegram |
| `TELEGRAM_CHAT_ID` | chat destino |
| `SEC_USER_AGENT` | identificacion para SEC |
| `SAM_GOV_API_KEY` | SAM.gov |
| `CONGRESS_GOV_API_KEY` | Congress.gov en produccion |
| `FEC_API_KEY` | OpenFEC en produccion |
| `GRANTS_GOV_API_KEY` | reservado si Grants.gov requiere clave |
| `USPTO_API_KEY` | USPTO, pendiente de validacion viva |

`.env.example` contiene solo nombres de variables y valores vacios.

No se deben versionar secretos, `.env`, `.env.local` ni `credentials.json`.

## Garantias actuales

V1 garantiza:

* fuentes integradas separadas por raw tabs;
* loaders idempotentes;
* deduplicacion por claves de fuente;
* generacion deterministica de hojas derivadas;
* tolerancia a fuentes faltantes en rebuild;
* preservacion de campos manuales en `review_queue`;
* scoring auditable;
* Telegram deduplicado;
* validaciones locales con fixtures;
* validaciones contra Google Sheets para calidad raw;
* documentacion de fuentes y scoring.

## Limitaciones conocidas

### Entity resolution

No existe resolucion avanzada de entidades.

El sistema conserva nombres, tickers, CIK, UEI u otros identificadores cuando
estan disponibles, pero no inventa correspondencias entre entidades.

### Fuente de verdad

Google Sheets sigue siendo la fuente de verdad.

Los archivos locales en `data/` son artefactos de extraccion y validacion.

### Fuentes externas

Las fuentes externas pueden fallar por:

* rate limits;
* indisponibilidad temporal;
* cambios de endpoint;
* credenciales ausentes;
* ventanas sin datos.

El workflow intenta continuar usando los datos historicos ya cargados en Google
Sheets si una fuente falla en una ejecucion diaria.

### USPTO

USPTO no esta validado en vivo.

Debe permanecer bloqueado hasta tener una API key oficial y muestras reales.

### 13F

SEC 13F esta integrado como metadata de filing.

El parsing detallado de holdings no forma parte de V1 hasta demostrar utilidad
con datos reales y mantenerlo auditable.

### No incluido en V1

V1 no incluye:

* dashboard;
* base de datos;
* backtesting;
* scoring predictivo;
* alertas por otros canales;
* Telegram interactivo;
* Google Sheets como UI avanzada;
* recomendaciones de trading;
* automatizacion de operaciones;
* modelos de IA.

## Criterio de producto cerrado

La version V1 se considera cerrada cuando:

* las fuentes V1 accesibles tienen collectors;
* las fuentes V1 validadas tienen loaders a raw tabs;
* el rebuild completo genera hojas derivadas;
* `review_queue` conserva estado y notas manuales;
* Telegram produce mensajes claros y deduplicados;
* scoring es parametrizable y documentado;
* validaciones locales pasan;
* calidad raw en Google Sheets pasa;
* secretos no estan versionados;
* las limitaciones quedan documentadas.

Este estado se alcanza con el producto actual, salvo las validaciones vivas que
dependen de credenciales externas no disponibles, como USPTO.

## Siguiente paso recomendado

El siguiente paso no es anadir mas fuentes.

El siguiente paso recomendado es observar varias ejecuciones diarias reales del
workflow y ajustar solo pesos de scoring o filtros de ruido cuando haya evidencia
operativa en `review_queue` y Telegram.
