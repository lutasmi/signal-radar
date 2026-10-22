# Signal Radar Scoring

Este documento explica como funciona el scoring operativo de Signal Radar en V1.

El scoring no es un modelo predictivo, no usa inteligencia artificial y no intenta
decidir si una oportunidad es buena o mala. Es una puntuacion deterministica,
simple y auditable para ordenar la `review_queue` y decidir que merece atencion
en Telegram.

## Donde se calcula

El score se calcula al construir `review_queue`.

Flujo:

```text
raw_* sheets
-> signals
-> cluster_signals
-> correlation_signals
-> priority_signals
-> review_queue
```

Archivos principales:

* `config/scoring.json`: pesos y bandas configurables.
* `radar/scoring.py`: motor de calculo del score.
* `scripts/build_review_queue_sheet.py`: aplica el score a cada fila de
  `review_queue`.
* `scripts/send_telegram_alerts.py`: muestra el score y su explicacion en el
  mensaje de Telegram.

## Que fila recibe score

El score se asigna a cada oportunidad de `review_queue`.

Una oportunidad puede venir de:

* un cluster de actividad repetida;
* una correlacion entre fuentes;
* actividad publica oficial repetida;
* una senal contractual, legislativa, regulatoria, de lobby, SEC, FEC o grants
  que haya escalado a prioridad.

Las filas `CLOSED` se conservan por historial. Si ya tenian score, no se
recalculan para evitar cambiar retrospectivamente el historial.

## Formula general

El score es una suma de componentes:

```text
score =
  peso por priority_level
+ peso por status
+ peso por review_today
+ peso por diversidad de fuentes
+ peso por opportunity_type
+ peso por tipos de senal vinculados
+ peso por recencia
```

Cada componente que suma puntos se guarda en `score_reason`.

Ejemplo:

```text
priority HIGH +50;
status ACTIVE +5;
review_today +15;
source diversity 2 sources +6;
CROSS_SOURCE +18;
PTR_BUY +8;
INSIDER_BUY +10;
recency <= 7d +14
```

Telegram convierte esa explicacion a texto mas legible:

```text
Scoring:
- Total: 126 (HIGH)
- Metodo: suma configurable de prioridad, estado, diversidad de fuentes,
  tipo de oportunidad, tipos de senal y recencia.
- Prioridad HIGH: +50
- Estado ACTIVE: +5
- Revisar hoy: +15
- Diversidad de fuentes: 2 sources +6
- Tipo/senal CROSS_SOURCE: +18
- Tipo/senal PTR_BUY: +8
- Tipo/senal INSIDER_BUY: +10
- Recencia <= 7d +14
```

## Componentes actuales

### 1. Prioridad base

Viene de `priority_signals.priority_level`.

| priority_level | puntos |
| --- | ---: |
| HIGH | 50 |
| MEDIUM | 30 |
| LOW | 10 |

Esto hace que una oportunidad ya priorizada como HIGH parta por encima de una
oportunidad MEDIUM o LOW.

### 2. Estado de ciclo de vida

Viene de `review_queue.status`.

| status | puntos |
| --- | ---: |
| NEW | 20 |
| ACTIVE | 5 |
| CLOSED | 0 |

Las oportunidades nuevas reciben mas peso porque requieren primera revision.
Las activas conservan algo de peso. Las cerradas no deberian generar alerta.

### 3. Revision del dia

Si `review_today = YES`, suma 15 puntos.

`review_today` es `YES` cuando:

* la oportunidad es nueva; o
* la oportunidad sigue abierta y su prioridad es HIGH.

### 4. Diversidad de fuentes

Por cada fuente adicional despues de la primera, suma 6 puntos.

Ejemplos:

| fuentes | calculo | puntos |
| --- | --- | ---: |
| 1 fuente | sin bonus | 0 |
| 2 fuentes | 1 fuente extra x 6 | 6 |
| 3 fuentes | 2 fuentes extra x 6 | 12 |

La idea es que una coincidencia entre familias distintas de datos merece mas
revision que una senal aislada dentro de una sola fuente.

### 5. Tipo de oportunidad

Viene de `review_queue.opportunity_type`.

| opportunity_type | puntos |
| --- | ---: |
| CONTRACT_MARKET_ENTITY | 20 |
| CROSS_SOURCE | 18 |
| PUBLIC_CATALYST_ACTIVITY | 14 |
| TICKER_CLUSTER_RELATED_SIGNAL | 12 |
| TICKER_ACTIVITY | 10 |
| REPEATED_CONTRACTS | 8 |

Este componente refleja la relevancia estructural de la oportunidad.

### 6. Tipos de senal vinculados

El score mira los `signal_ids` vinculados a la oportunidad y suma una sola vez
por cada `signal_type` distinto.

Ejemplo: si una oportunidad tiene tres `PTR_BUY`, solo suma `PTR_BUY` una vez.

Pesos actuales:

| signal_type | puntos |
| --- | ---: |
| PTR_BUY | 8 |
| PTR_SELL | 2 |
| INSIDER_BUY | 10 |
| CONTRACT | 6 |
| CONTRACT_OPPORTUNITY | 5 |
| CONTRACT_PRE_SOLICITATION | 6 |
| CONTRACT_SOLE_SOURCE | 8 |
| CONTRACT_AWARD_NOTICE | 7 |
| BILL_INTRODUCED | 4 |
| COMMITTEE_ACTION | 6 |
| LEGISLATIVE_ACTION | 5 |
| RULE_PROPOSED | 6 |
| RULE_FINAL | 8 |
| AGENCY_NOTICE | 4 |
| EXECUTIVE_ACTION | 7 |
| REGULATORY_CATALYST | 5 |
| LOBBY_NEW_REGISTRATION | 7 |
| LOBBY_SPEND_INCREASE | 8 |
| LOBBY_ISSUE_MATCH | 5 |
| LOBBY_CLIENT_ACTIVITY | 4 |
| MATERIAL_EVENT_8K | 8 |
| MATERIAL_CONTRACT_8K | 9 |
| MAJOR_HOLDER_ENTRY | 8 |
| ACTIVIST_POSITION | 9 |
| FORM144_PLANNED_SALE | 5 |
| INSTITUTIONAL_POSITION_13F | 3 |
| POLITICAL_CONTRIBUTION | 4 |
| PAC_CONTRIBUTION | 5 |
| EXECUTIVE_CONTRIBUTION | 7 |
| GRANT_FORECAST | 5 |
| GRANT_OPPORTUNITY | 6 |

### 7. Recencia

La recencia compara `review_queue.last_date` con la fecha de ejecucion del
radar.

| antiguedad | puntos |
| --- | ---: |
| hasta 7 dias | 14 |
| hasta 30 dias | 8 |
| hasta 90 dias | 3 |
| mas de 90 dias | 0 |

La recencia solo suma si la fecha de la senal es igual o anterior a la fecha de
ejecucion.

## Fechas futuras

La frase "no premiar eventos posteriores al run date" significa esto:

Si el radar se ejecuta el `2026-07-05` y una fuente trae una fecha
`2026-12-31`, esa fila puede conservarse porque es un dato oficial, pero no debe
recibir puntos de recencia.

Antes de la correccion, una fecha futura podia comportarse como si fuera "hoy"
por el calculo de edad negativa normalizada a cero. Eso podia darle
incorrectamente el bonus maximo de recencia:

```text
recency <= 7d +14
```

Ahora, si `last_date > run_date`, la recencia suma 0 puntos.

Importante:

* la fila no se descarta;
* el dato bruto se conserva;
* el resto del score puede seguir aplicando;
* solo se evita el bonus de "reciente".

Esto es especialmente relevante para fuentes como FEC o Federal Register, que
pueden devolver registros con fechas futuras, programadas o posteriores a la
fecha de ejecucion local.

## Bandas de score

Despues de sumar puntos, el score se convierte en banda:

| score | score_band |
| ---: | --- |
| 85 o mas | HIGH |
| 45 a 84 | MEDIUM |
| 0 a 44 | LOW |

Estas bandas se usan para ordenar `review_queue` y para decidir alertas.

## Regla de Telegram

Telegram alerta una oportunidad si:

* `status = NEW`; o
* `review_today = YES`; o
* `score_band = HIGH`.

Excepcion: `status = CLOSED` nunca genera alerta aunque conserve un score alto
historico.

Cada mensaje incluye:

* asunto;
* quien compra o actua;
* que compra, recibe o queda afectado;
* vinculacion o relevancia;
* detalle de senales vinculadas;
* score total;
* explicacion de cada componente del score.

## Como parametrizarlo

Los pesos se cambian en:

```text
config/scoring.json
```

Despues de modificar pesos o bandas, ejecutar:

```bash
python3 scripts/validate_all.py
python3 scripts/rebuild_radar.py
python3 scripts/send_telegram_alerts.py --dry-run
```

`validate_all.py` comprueba que la configuracion tenga las secciones necesarias
y que los pesos sean numericos.

## Principios de diseno

El scoring V1 debe seguir siendo:

* explicable;
* deterministicamente reproducible;
* parametrizable sin editar Python;
* lo bastante simple para auditarlo en Google Sheets;
* no predictivo;
* no conectado a decisiones automaticas de trading.

El score ordena trabajo humano. No sustituye la revision.
