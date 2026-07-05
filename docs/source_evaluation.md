# Candidate Official Source Evaluation

Local extraction remains file-first. These sources are not connected to Google
Sheets, scoring, Telegram, or entity resolution. Live raw responses are kept
locally under ignored `data/raw/<source>/` paths, and normalized CSV output is
kept under ignored `data/processed/<source>/` paths.

Allowed source states:

* `VALIDATED_LOCAL`: live official data was recovered locally and validations passed.
* `BLOCKED_CREDENTIALS`: the collector is prepared, but live validation needs credentials.
* `REJECTED`: evidence is sufficient to discard the source for now.

## Live Audit

Execution timestamps are UTC run identifiers from generated filenames.

### SAM.gov

Command: `python3 collectors/collect_sam_gov.py --days 30 --limit 5 --max-pages 2`

Execution: `20260705T145155Z`

Endpoint: `https://api.sam.gov/opportunities/v2/search`

Parameters: `postedFrom` and `postedTo` covering the last 30 days, `limit=5`,
`offset=0` and `offset=5`.

Raw records: 10.

Processed records: 10.

Raw output:

* `data/raw/sam_gov/sam_gov_20260705T145155Z_page_1.json`
* `data/raw/sam_gov/sam_gov_20260705T145155Z_page_2.json`

Processed output:

* `data/processed/sam_gov/sam_gov_20260705T145155Z.csv`

Main fields: `notice_id`, `title`, `solicitation_number`, `posted_date`,
`response_deadline`, `archive_date`, `last_modified_date`, `type`, `base_type`,
`signal_type`, organization path, NAICS, classification code, award fields,
awardee name, awardee UEI.

Sample records:

| notice_id | title | posted_date | response_deadline | archive_date | type |
| --- | --- | --- | --- | --- | --- |
| `ff09d0a8201c4093a448448e722623ab` | 53--PIN,CENTRIFUGAL TRIP | 2026-07-05 | 2026-07-13 | 2026-08-12 | Combined Synopsis/Solicitation |
| `fece4d2d6ddc441cad670bc8bcebd5b6` | 41--REFRIGERATOR-FREEZE | 2026-07-05 | 2026-07-09 | 2026-08-08 | Combined Synopsis/Solicitation |
| `fdacc023380a466d9535fe2435447e20` | 53--BOLT,SHEAR | 2026-07-05 | 2026-07-13 | 2026-08-12 | Combined Synopsis/Solicitation |

Usefulness criterion: each opportunity has an official notice ID, opportunity
type, publication date, deadline/archive dates when present, and enough agency
and award metadata to derive `CONTRACT_OPPORTUNITY`,
`CONTRACT_PRE_SOLICITATION`, `CONTRACT_SOLE_SOURCE`, and
`CONTRACT_AWARD_NOTICE` candidates. Pagination produced two distinct raw pages
with no duplicate `notice_id` values in processed output.

### Congress.gov

Command: `python3 collectors/collect_congress_gov.py --use-demo-key --limit 5 --max-pages 2`

Execution: `20260705T145218Z`

Endpoint: `https://api.congress.gov/v3/bill`

Parameters: `format=json`, `limit=5`, `offset=0` and `offset=5`.

Raw records: 10.

Processed records: 10.

Raw output:

* `data/raw/congress_gov/congress_gov_20260705T145218Z_page_1.json`
* `data/raw/congress_gov/congress_gov_20260705T145218Z_page_2.json`

Processed output:

* `data/processed/congress_gov/congress_gov_20260705T145218Z.csv`

Main fields: bill ID, congress, bill type, bill number, title, origin chamber,
introduced date, latest action date/text, sponsor, bioguide ID, policy area.

Sample records:

| bill_id | title | origin_chamber | latest_action_date |
| --- | --- | --- | --- |
| `110-hconres-12` | Requiring the display of the Ten Commandments in the United States Capitol. | House | 2007-01-05 |
| `110-hconres-22` | Expressing the sense of Congress that the President should provide notice of withdrawal of the United States from the North American Free Trade Agreement (NAFTA). | House | 2007-01-11 |
| `110-hconres-20` | Calling on the Government of the United Kingdom to immediately establish a full, independent, and public judicial inquiry into the murder of Northern Ireland defense attorney Patrick Finucane, as recommended by Judge Peter Cory as part of the Weston Park Agreement, in order to move forward on the Northern Ireland peace process. | House | 2007-03-19 |

Usefulness criterion: bill identity and legislative action dates are stable
enough for `BILL_INTRODUCED`, `COMMITTEE_ACTION`, and `LEGISLATIVE_ACTION`.
Cosponsor detail needs a later endpoint pass.

### Federal Register

Command: `python3 collectors/collect_federal_register.py --per-page 5 --max-pages 2`

Execution: `20260705T145222Z`

Endpoint: `https://www.federalregister.gov/api/v1/documents.json`

Parameters: `per_page=5`, `page=1` and `page=2`, `order=newest`.

Raw records: 10.

Processed records: 10.

Raw output:

* `data/raw/federal_register/federal_register_20260705T145222Z_page_1.json`
* `data/raw/federal_register/federal_register_20260705T145222Z_page_2.json`

Processed output:

* `data/processed/federal_register/federal_register_20260705T145222Z.csv`

Main fields: document number, publication date, type, signal type, title,
agencies, CFR references, HTML/PDF URLs, abstract.

Sample records:

| document_number | publication_date | type | signal_type | title |
| --- | --- | --- | --- | --- |
| `2026-13639` | 2026-07-06 | Proposed Rule | RULE_PROPOSED | Improvements to Rules on Recoupment of Benefit Overpayments |
| `2026-13637` | 2026-07-06 | Rule | RULE_FINAL | Rescission of Guidelines on Affirmative Action Appropriate Under Title VII of the Civil Rights Act of 1964, as Amended |
| `2026-13631` | 2026-07-06 | Presidential Document | EXECUTIVE_ACTION | Presidential Determination on Assistance to Venezuela Consistent With the Trafficking Victims Protection Act of 2000 |

Usefulness criterion: official document number, agency, type, and publication
date directly support `RULE_PROPOSED`, `RULE_FINAL`, `AGENCY_NOTICE`,
`EXECUTIVE_ACTION`, and `REGULATORY_CATALYST`.

### LDA.gov

Command: `python3 collectors/collect_lda_gov.py --filing-year 2026 --page-size 5 --max-pages 2`

Execution: `20260705T145218Z`

Endpoint: `https://lda.senate.gov/api/v1/filings/`

Parameters: `filing_year=2026`, `page_size=5`, `page=1` and `page=2`,
`ordering=-dt_posted`.

Raw records: 10.

Processed records: 10.

Raw output:

* `data/raw/lda_gov/lda_gov_20260705T145218Z_page_1.json`
* `data/raw/lda_gov/lda_gov_20260705T145218Z_page_2.json`

Processed output:

* `data/processed/lda_gov/lda_gov_20260705T145218Z.csv`

Main fields: filing UUID, filing type, filing year/period, posted date,
registrant ID/name, client ID/name, income, expenses, issue codes, government
entities.

Sample records:

| filing_uuid | filing_type | posted_date | registrant_name |
| --- | --- | --- | --- |
| `5e0160a0-5a67-4c13-a8d3-027bb5d68ae8` | Q2 | 2026-07-05 | VAN BUSKIRK AND ASSOCIATES LLC |
| `efd982bd-bdc2-47d5-bbb9-1d959c091068` | Q2 | 2026-07-05 | LMH STRATEGIC SOLUTIONS |
| `397eae69-794d-46e4-8a1f-6114e6a444a1` | Q2 | 2026-07-05 | LMH STRATEGIC SOLUTIONS |

Usefulness criterion: registrant, client, issue, government-entity, spend, and
posted-date fields support `LOBBY_NEW_REGISTRATION`, `LOBBY_SPEND_INCREASE`,
`LOBBY_ISSUE_MATCH`, and `LOBBY_CLIENT_ACTIVITY`.

### SEC EDGAR Additional

Command: `python3 collectors/collect_sec_edgar_additional.py --count 10 --max-pages 1 --delay 0.1`

Execution: `20260705T145241Z`

Endpoint: `https://www.sec.gov/cgi-bin/browse-edgar`

Parameters: Atom current filings feed with forms `8-K`, `SC 13D`, `SC 13G`,
`144`, and `13F-HR`, `count=10`, `start=0`.

Raw records: 30.

Processed records: 25.

Duplicates removed: 5 duplicate form/accession rows. Raw XML files are
preserved unchanged.

Raw output:

* `data/raw/sec_edgar_additional/sec_edgar_additional_20260705T145241Z_8_k_page_1.xml`
* `data/raw/sec_edgar_additional/sec_edgar_additional_20260705T145241Z_sc_13d_page_1.xml`
* `data/raw/sec_edgar_additional/sec_edgar_additional_20260705T145241Z_sc_13g_page_1.xml`
* `data/raw/sec_edgar_additional/sec_edgar_additional_20260705T145241Z_144_page_1.xml`
* `data/raw/sec_edgar_additional/sec_edgar_additional_20260705T145241Z_13f_hr_page_1.xml`

Processed output:

* `data/processed/sec_edgar_additional/sec_edgar_additional_20260705T145241Z.csv`

Main fields: accession, form type, signal type, company name, CIK, filing date,
updated date, source URL.

Sample records:

| accession | form_type | signal_type | company_name | filing_date |
| --- | --- | --- | --- | --- |
| `0001683168-26-005262` | 8-K | MATERIAL_EVENT_8K | 8-K - AppTech Payments Corp. | 2026-07-02 |
| `0001193125-26-294994` | 8-K | MATERIAL_EVENT_8K | 8-K - Seer, Inc. | 2026-07-02 |
| `0001437749-26-022589` | 8-K | MATERIAL_EVENT_8K | 8-K - Picard Medical, Inc. | 2026-07-02 |

Usefulness criterion: accession, CIK, form type, and filing date support
`MATERIAL_EVENT_8K`, `MAJOR_HOLDER_ENTRY`, `FORM144_PLANNED_SALE`, and a
candidate `INSTITUTIONAL_POSITION_13F`. 13F remains metadata-only until holdings
parsing proves useful.

### FEC

Command: `python3 collectors/collect_fec.py --use-demo-key --period 2026 --per-page 5 --max-pages 2`

Execution: `20260705T145241Z`

Endpoint: `https://api.open.fec.gov/v1/schedules/schedule_a/`

Parameters: `two_year_transaction_period=2026`, `min_date=2025-01-01`,
`max_date=2026-12-31`, `per_page=5`, cursor pagination from `last_indexes`.

Raw records: 10.

Processed records: 10.

Rows without usable contribution date: 0.

Raw output:

* `data/raw/fec/fec_20260705T145241Z_page_1.json`
* `data/raw/fec/fec_20260705T145241Z_page_2.json`

Processed output:

* `data/processed/fec/fec_20260705T145241Z.csv`

Main fields: transaction ID, committee ID/name, contributor name, employer,
occupation, city/state, contribution date, amount, transaction period.

Sample records:

| transaction_id | committee_name | contributor_name | contribution_receipt_date |
| --- | --- | --- | --- |
| `A53FDC6790CB44186905` | BANKS FOR SENATE | WELLS, VIRGINIA | 2026-12-31 |
| `11135250` | DEMOCRATIC EXECUTIVE COMMITTEE OF FLORIDA | FLORIDA PARTY VICTORY FUND | 2026-12-31 |
| `11091835` | OHIO DEMOCRATIC PARTY - FEDERAL | DEMOCRATIC NATIONAL COMMITTEE | 2026-12-31 |

Usefulness criterion: contribution transaction ID, contributor, committee,
amount, and date support `POLITICAL_CONTRIBUTION`, `PAC_CONTRIBUTION`, and
candidate `EXECUTIVE_CONTRIBUTION`. Cross-source donation proximity is deferred.

### Grants.gov

Command: `python3 collectors/collect_grants_gov.py --keyword energy --rows 5 --max-pages 2`

Execution: `20260705T145242Z`

Endpoint: `https://api.grants.gov/v1/api/search2`

Parameters: POST payload with `keyword=energy`, `oppStatuses=forecasted|posted`,
`rows=5`, `startRecordNum=0` and `startRecordNum=5`.

Raw records: 10.

Processed records: 10.

Raw output:

* `data/raw/grants_gov/grants_gov_20260705T145242Z_page_1.json`
* `data/raw/grants_gov/grants_gov_20260705T145242Z_page_2.json`

Processed output:

* `data/processed/grants_gov/grants_gov_20260705T145242Z.csv`

Main fields: opportunity ID, opportunity number, title, agency code/name,
open/close dates, opportunity status, document type, CFDA list.

Sample records:

| opportunity_id | opportunity_number | agency_code | open_date | close_date |
| --- | --- | --- | --- | --- |
| `356623` | DE-FOA-0003467 | DOE-ARPAE | 2024-10-02 | 2029-09-29 |
| `329436` | DE-FOA-0002265 | DOE-ID | 2020-10-15 | 2030-10-14 |
| `361628` | DE-FOA-0003548 | DOE-GFO | 2026-03-25 | 2026-07-24 |

Usefulness criterion: opportunity IDs, agencies, statuses, and open/close dates
support `GRANT_FORECAST` and `GRANT_OPPORTUNITY`. Award-related signals need a
separate official detail/award validation.

### USPTO

Command: `python3 collectors/collect_uspto.py --rows 1 --max-pages 1`

Endpoint prepared: `https://api.uspto.gov/api/v1/patent/applications/search`

Variable required: `USPTO_API_KEY`.

Procedure to obtain key: request access through the official USPTO API catalog
or USPTO developer account flow, then set `USPTO_API_KEY` locally or in CI
secrets.

Command once key exists:

```bash
python3 collectors/collect_uspto.py --query Tesla --rows 20 --max-pages 1
```

Offline validations already passing: field normalization, required IDs/dates,
empty-response handling, auth/rate-limit classification, and deterministic
output checks.

Pending validations: live official records, raw response preservation,
pagination, and real-field usefulness.

Real error received without credentials:

```text
USPTO_API_KEY is required for the official USPTO API endpoint
```

## Final Evaluation Table

| Source | Status | Real data recovered | Records | Raw output | Processed output | Pagination validated | Deduplication | Date handling | Quality | Signal potential | Limitations | Final decision |
| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SAM.gov | `VALIDATED_LOCAL` | Yes | 10 | `data/raw/sam_gov/sam_gov_20260705T145155Z_page_*.json` | `data/processed/sam_gov/sam_gov_20260705T145155Z.csv` | Yes, `offset` pages 0 and 5 | No duplicate `notice_id` values | Posted, deadline, archive, and modified dates preserved when present | High | Contract opportunities, pre-solicitations, sole source, awards | Requires API key for live runs | Keep as validated local candidate |
| Congress.gov | `VALIDATED_LOCAL` | Yes | 10 | `data/raw/congress_gov/congress_gov_20260705T145218Z_page_*.json` | `data/processed/congress_gov/congress_gov_20260705T145218Z.csv` | Yes, `offset` pages 0 and 5 | Unique bill IDs | Latest action dates preserved; introduced date absent in sample rows | Medium-high | Bills and legislative actions | Cosponsors/detail endpoints not yet collected | Keep as validated local candidate |
| Federal Register | `VALIDATED_LOCAL` | Yes | 10 | `data/raw/federal_register/federal_register_20260705T145222Z_page_*.json` | `data/processed/federal_register/federal_register_20260705T145222Z.csv` | Yes, page 1 and 2 | Unique document numbers | Publication dates parsed as ISO dates | High | Proposed/final rules, notices, executive actions | Legal text review may need PDFs for deeper use | Keep as validated local candidate |
| LDA.gov | `VALIDATED_LOCAL` | Yes | 10 | `data/raw/lda_gov/lda_gov_20260705T145218Z_page_*.json` | `data/processed/lda_gov/lda_gov_20260705T145218Z.csv` | Yes, page 1 and 2 | Unique filing UUIDs | Posted timestamps normalized to dates | High | Lobby registrations, spend, issues, client activity | Senate endpoint migration should be monitored | Keep as validated local candidate |
| SEC EDGAR additional | `VALIDATED_LOCAL` | Yes | 25 processed from 30 raw | `data/raw/sec_edgar_additional/sec_edgar_additional_20260705T145241Z_*_page_1.xml` | `data/processed/sec_edgar_additional/sec_edgar_additional_20260705T145241Z.csv` | Yes, per-form feed page | 5 duplicate form/accession rows removed; raw preserved | Filing and updated dates preserved | Medium | 8-K, 13D/G, 144, 13F metadata | 13F holdings and 8-K item parsing not implemented | Keep as validated local candidate; keep 13F under observation |
| FEC | `VALIDATED_LOCAL` | Yes | 10 | `data/raw/fec/fec_20260705T145241Z_page_*.json` | `data/processed/fec/fec_20260705T145241Z.csv` | Yes, cursor `last_indexes` | Unique transaction IDs checked | Reproducible `min_date`/`max_date`; 0 rows without dates | Medium | Political/PAC/executive contributions | Entity matching and event proximity deferred | Keep as validated local candidate |
| Grants.gov | `VALIDATED_LOCAL` | Yes | 10 | `data/raw/grants_gov/grants_gov_20260705T145242Z_page_*.json` | `data/processed/grants_gov/grants_gov_20260705T145242Z.csv` | Yes, `startRecordNum` 0 and 5 | Unique opportunity IDs | Open/close dates normalized to ISO dates | Medium-high | Grant forecasts and opportunities | Award-related signal not validated | Keep as validated local candidate |
| USPTO | `BLOCKED_CREDENTIALS` | No | 0 | N/A | N/A | Offline only | Offline only | Offline normalization only | Unknown | Patent applications/activity if key works | Needs `USPTO_API_KEY`; no live validation simulated | Keep blocked until credentials exist |
