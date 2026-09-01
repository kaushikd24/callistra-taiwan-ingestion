# PROJECT OVERVIEW

This is Callistra, a financial markets research platform, where we ingest stock exchange filings, financial filings, company IR material, regulatory documents, clinical trials, etc. and made the entire corpus retrievable for end users and AI Agents. The product is an enterprise SaaS, all systems are proprietary.


# Adding a New Ingestion Source

This document is the shared guide for bringing any new source (Deutsche Börse,
Euronext, RBI circulars, news feeds, etc.) into the Callistra pipeline. It
covers both the OCR runner and the embedding pipeline. New source = write the
ingestion code, populate `documents` correctly, and both runners pick it up
automatically. No changes to either runner are needed.

---

## Architecture overview

```
Ingestion pipeline
  └─ writes → documents (ingestion_status='ingested') + sidecar table
       │       sets translation_required=true/false
       ▼
  [OCR runner]              ← picks up ingestion_status='ingested', ocr_path IS NULL
  runs OCR if needed,       ← skips if source is machine-readable (HTML/XML)
  writes ocr_path,          ← sets ingestion_status='ocr_completed'
       │
       ├─ translation_required=false → skip to embedding
       │
       ▼
  [Translation pipeline]    ← picks up translation_required=true, translation_status='pending'
  translates ocr_path,      ← writes translated_ocr_path
  sets translation_status='translated'
       │
       ▼
  [Embedding pipeline]      ← picks up ingestion_status='ocr_completed'
                               AND (translation_required=false OR translation_status='translated')
  TEXT stage → embeds text chunks into MongoDB (callistra-main-mongodb-collection)
  TABLE stage → embeds tables into MongoDB (callistra-table-search)
                inserts rows into document_tables (Postgres)
  writes → documents only (ingestion_status, indexed_in_vector, vector_db_doc_id)
```

The `documents` table is the single integration point. Sidecar tables capture
source-specific raw fields and ingestion lifecycle — the OCR and embedding
runners never write to sidecar tables.

---

## The `documents` table contract

### Required at ingestion time

| Column | Type | Notes |
|---|---|---|
| `source_table` | text | Name of the sidecar table, e.g. `'deutsche_boerse_documents'` |
| `source_row_id` | uuid | PK of the matching sidecar row (enforces the unique constraint) |
| `source_system` | text | e.g. `'deutsche_boerse'`, `'euronext'`, `'rbi'` |
| `source_type` | text | e.g. `'regulatory_filing'`, `'press_release'`, `'news'` |
| `doc_name` | text | Filename or short identifier, shown in citations |
| `blob_path` | text | `s3://bucket/key` of the original source file (PDF, HTML, etc.) |
| `ingestion_status` | text | Set to `'ingested'` at write time |
| `canonical_doc_type` | text | Callistra taxonomy value — see taxonomy reference |
| `entity_type` | text | Usually `'company'`; `'government'` for regulatory bodies |
| `published_at` | timestamptz | Document publication date |

### Populated by OCR runner (not ingestion)

| Column | Notes |
|---|---|
| `ocr_path` | `s3://bucket/key` of the `.mmd` embedding markdown file |
| `ingestion_status` | Updated to `'ocr_completed'` when OCR finishes |
| `ocr_completed_at` | Timestamp set when OCR finishes |

### Optional but strongly recommended

| Column | Notes |
|---|---|
| `title` | Full document title — used in search results |
| `company_name` | Issuer / entity name |
| `primary_symbol` | Primary ticker, e.g. `'DBK'` |
| `symbols` | `text[]` — all tickers this document is relevant to |
| `exchange` | e.g. `'XETRA'`, `'EURONEXT'`, `'NSE'` |
| `country` | human readable `'INDIA'`, `'USA'`, `'UK'` |
| `country_code` | `char(2)` — ISO alpha-2 style: DE, GB, IN, US |
| `fiscal_year` | `smallint` |
| `fiscal_quarter` | `'Q1'`–`'Q4'` |

### Written by the embedding pipeline (never touch these in ingestion)

`vector_db_doc_id`, `indexed_in_vector`, `embeddings_completed_at`,
`ready_for_search_at`, `retry_count`, `processing_error`, `ingestion_status`
(after `ocr_completed`).

---

## `ingestion_status` lifecycle

```
'ingested'             ← set by ingestion pipeline (PDF sources)
    │                     or skip directly to 'ocr_completed' (machine-readable sources)
    │  [OCR runner — claim]
    ▼
'ocr_in_progress'      ← set atomically when the OCR runner claims the row
    │
    │  [OCR runner — finish]
    ▼
'ocr_completed'        ← set by OCR runner on success (or by ingestion if no OCR needed)
    │
    │  [Embedding pipeline — TEXT stage]
    ▼
'embedding_complete'   ← set by embedding pipeline after text chunks are in MongoDB
    │
    │  [Embedding pipeline — TABLE stage]
    ▼
'ready_for_analytics'  ← set by embedding pipeline after tables are in MongoDB

'failed'               ← set by OCR runner or embedding pipeline on unrecoverable error
                          processing_error column holds the exception message
```

**If your source is machine-readable** (HTML, XML — no OCR needed): the
ingestion pipeline sets `ingestion_status = 'ocr_completed'` directly and
writes the `.mmd` path to both `blob_path` and `ocr_path`. The OCR runner
never sees this row. LSEG works this way.

**If your source needs OCR** (PDFs, scanned images): set
`ingestion_status = 'ingested'` and leave `ocr_path` NULL. The OCR runner
picks it up, runs OCR, writes `ocr_path`, and sets `'ocr_completed'`.
NSE/BSE and SEC PDF documents work this way.

---

## What the OCR runner does

### Overview

The OCR runner (`live_pipeline/live_runner.py`) is a single long-running
process on the OCR VM. It polls `documents` on a configurable interval,
claims rows that need OCR, runs the pipeline, and writes the result back —
all on the `documents` table. It never reads or writes any sidecar table.

### Claim and process loop

Each cycle:

1. **Claim** — atomically transition up to N rows from `ingestion_status =
   'ingested'` to `'ocr_in_progress'` using a single `UPDATE ... WHERE id IN
   (SELECT ... FOR UPDATE SKIP LOCKED)`. Two concurrent workers on different
   VMs can never claim the same row.

2. **Download** — fetch `documents.blob_path` from S3 to a local temp file.

3. **Normalise** — the PDF is passed through `NormalisePDF` which standardises
   page size and DPI before the model sees it.

4. **Detect** — `RTDetrHeronTableDetector` (RT-DETRv2 + Heron layout model,
   loaded from `docling-project/docling-layout-heron` on HuggingFace Hub)
   identifies page regions and table bounding boxes.

5. **Extract** — `HFTATRExtractor` (TATR, loaded from
   `microsoft/table-transformer-structure-recognition`) reconstructs the row/
   column grid for each detected table.

6. **Render** — output is written as a `.mmd` file in the embedding markdown
   format (see below). Each table appears twice: as a pipe-markdown table and
   as a `<table>` HTML block.

7. **Upload** — the `.mmd` is uploaded to `s3://callistra-general-ocr/{prefix}/{key}.mmd`.
   The output prefix is determined by `documents.source_table`:

   | `source_table` | S3 prefix |
   |---|---|
   | `nse_documents` | `nse/` |
   | `broker_reports` | `broker_report/` |
   | `sec_documents` | `sec/` |
   | `lseg_documents` | `lseg/` |
   | _(new source)_ | `{source_table}/` |

   The rest of the key is taken directly from the `blob_path` key with the
   extension replaced by `.mmd`. No source-specific path manipulation.

8. **Complete** — on success, `documents.ocr_path` is set to the uploaded
   S3 URI, `ingestion_status` is set to `'ocr_completed'`, and
   `ocr_completed_at` is stamped. The embedding pipeline picks it up from here.

9. **Failure** — on any exception, `ingestion_status` is set to `'failed'`
   and `processing_error` records the exception message. These rows are not
   retried automatically — manual intervention or a backfill script is needed.

### Models

Both models run CPU-only. They are loaded once at process startup and kept
resident across all documents processed in the lifetime of the service —
no per-document model reload.

| Model | HuggingFace id | Purpose |
|---|---|---|
| RT-DETRv2 + Heron | `docling-project/docling-layout-heron` | Page layout detection, table bounding boxes |
| TATR | `microsoft/table-transformer-structure-recognition` | Table row/column structure extraction |

The `docling` Python package is **not** used — models are loaded directly via
`transformers`. `timm` is required as a backbone dependency.

### Configuration (env / CLI)

| Flag | Default | Meaning |
|---|---|---|
| `--max-files` | 10 | Documents claimed per polling cycle |
| `--poll-interval` | 60 | Seconds between cycles |
| `--once` | — | Run one cycle and exit (useful for testing) |
| `--quiet` | — | Suppress per-document progress logs |

### What the runner does NOT do

- Does not read any sidecar table (`nse_documents`, `broker_reports`, etc.)
- Does not write any sidecar table
- Does not route differently per source — all PDFs go through the same pipeline
- Does not retry failed rows — `ingestion_status = 'failed'` is terminal until
  manually reset to `'ingested'`

### Adding a new PDF source

Zero changes to the OCR runner. As long as the ingestion pipeline writes a
`documents` row with `ingestion_status = 'ingested'`, `blob_path` pointing
to a valid S3 PDF, and `source_table` set to the sidecar table name, the
runner picks it up automatically on the next cycle. The output will land at
`s3://callistra-general-ocr/{source_table}/{blob_key}.mmd`.

---

## What the embedding pipeline does

### TEXT stage

Triggered by: `ingestion_status = 'ocr_completed' AND embeddings_completed_at IS NULL`

1. Downloads the `.mmd` file from `documents.ocr_path` (falls back to `blob_path`).
2. Splits on `<--- Page Split --->` markers to track page numbers.
3. Within each page, splits on `<table>…</table>` boundaries:
   - **Text segments**: cleaned (HTML stripped), accumulated into chunks of
     ≤ 1 500 tokens. Flushed at table boundaries or when the token target is hit.
   - **Table segments**: kept as atomic inline chunks — never split, never
     merged with adjacent text. Converted from HTML to markdown for embedding.
4. Each chunk is embedded via Azure OpenAI (`text-embedding-3-small`, 1536 dims).
5. Chunks are upserted into MongoDB `callistra-main-mongodb-collection` with
   `ordered=False` (retries are idempotent — duplicate chunk UUIDs are skipped).
6. On success: `documents.indexed_in_vector = true`,
   `documents.vector_db_doc_id` set to the MongoDB document id,
   `documents.ingestion_status = 'embedding_complete'`.

### TABLE stage

Triggered by: `indexed_in_vector = true AND ready_for_search_at IS NULL`

1. Downloads the same `.mmd` file.
2. Extracts all raw `<table>…</table>` HTML blocks with their page numbers.
3. Each table is BERT-classified into one of the valid financial table types
   (`FinancialStatement`, `RatioTable`, etc.) — tables that don't match are
   dropped.
4. Classified tables are inserted into `document_tables` (Postgres) with
   `ON CONFLICT (document_id, table_idx) DO UPDATE` — idempotent on retry.
5. `search_text` (labels/headers, numeric cells stripped) is embedded and
   inserted into MongoDB `callistra-table-search`.
6. The shared `document_tables.id` UUID is used as the MongoDB `_id` —
   one citation ID resolves both SQL (for rendering) and vector search.
7. On success: `documents.ingestion_status = 'ready_for_analytics'`.

### Retry behaviour

Both stages reset their claim marker on transient failure so the next cycle
retries. After `MAX_TABLE_RETRIES` (5) consecutive failures, the claim marker
is left set (permanent exclusion) and `ingestion_status = 'failed'`. Errors
are written to `documents.processing_error`.

---

## The `.mmd` embedding markdown format

Every source must produce a `.mmd` file in this exact format. The OCR runner
generates it for PDF sources; machine-readable sources generate it themselves
at ingestion time.

```
## Page 1

<text content for page 1>

| Col A | Col B |
|-------|-------|
| val   | val   |

<table>
  <tr><th>Col A</th><th>Col B</th></tr>
  <tr><td>val</td><td>val</td></tr>
</table>

<--- Page Split --->

## Page 2

<text content for page 2>
...
```

**Every table appears twice**: once as a pipe-markdown table (embedded inline
with the surrounding text in the TEXT stage), and once as a raw `<table>` HTML
block (extracted and embedded standalone by the TABLE stage). This is
intentional — do not omit either form.

Page splits use the exact string `<--- Page Split --->` (case-insensitive in
the parser, but use this casing for consistency). Page headers use `## Page N`
where N is the 1-based page number.

---

## Checklist for adding a new source

- [ ] Create a sidecar table with source-specific fields (raw API response
      fields, source-native IDs, etc.). Include `document_id uuid REFERENCES
      documents(id)` as the FK.
- [ ] Write the ingestion pipeline to INSERT into both the sidecar table and
      `documents` in one transaction. Set `ingestion_status = 'ingested'` on
      `documents`. Set `ingestion_status = 'ocr_completed'` instead if the
      source is machine-readable and you generate the `.mmd` yourself.
- [ ] For machine-readable sources: generate the `.mmd` in the embedding
      markdown format above, upload to S3, write the path to `documents.ocr_path`.
- [ ] Populate as many of the optional metadata columns as the source provides
      (`title`, `company_name`, `primary_symbol`, `symbols`, `exchange`,
      `country`, `fiscal_year`, `fiscal_quarter`).
- [ ] Set `source_table` to the exact name of your sidecar table.
- [ ] Set `source_row_id` to the sidecar row's PK.
- [ ] Verify with:
      ```sql
      SELECT ingestion_status, COUNT(*), COUNT(ocr_path)
      FROM documents
      WHERE source_table = '<your_sidecar_table>'
      GROUP BY ingestion_status;
      ```
      You should see `ocr_completed` rows with `ocr_path` populated before
      the embedding pipeline will process them.

---

## Canonical document types

Every ingested document **must** be mapped to one of Callistra's canonical document types. These values are used throughout search, analytics, filtering, document grouping, and downstream AI pipelines.

Do **not** invent new values unless the user explicitly requests a taxonomy change. If a document cannot be confidently mapped, use `other` and mention the ambiguity to the user.

Current supported canonical document types:

| Canonical Document Type | Typical Examples |
|--------------------------|------------------|
| `annual_report` | Annual reports, 10-K, 20-F, yearly reports |
| `financial_results` | Quarterly results, interim reports, earnings releases, financial statements |
| `earnings_transcript` | Earnings call transcripts |
| `earnings_call_update` | Earnings call announcements, webcast notices, analyst call invitations |
| `investor_presentation` | Investor presentations, slide decks, capital markets day presentations |
| `press_release` | Corporate press releases |
| `business_updates` | Business updates, operational updates, company developments |
| `general_disclosure` | General regulatory disclosures, 6-K, 8-K, miscellaneous exchange announcements |
| `management_change` | CEO/CFO appointments, resignations, executive management changes |
| `board_meeting` | Board meeting notices and outcomes |
| `shareholder_meeting` | AGM, EGM, shareholder meeting notices |
| `shareholder_communication` | Letters to shareholders and shareholder communications |
| `corporate_action` | Dividends, stock splits, bonus issues, buybacks, rights issues |
| `fundraising` | Equity raises, debt issuance, private placements, convertible securities |
| `mna_restructuring` | Acquisitions, mergers, divestitures, restructurings, spin-offs |
| `litigation_regulatory_action` | Litigation, enforcement actions, regulatory proceedings |
| `regulatory_compliance` | Compliance filings, governance reports, statutory disclosures |
| `regulatory_general` | Used for non-company specific regulatory sources such as but not limited to: "ClinicalTrials.gov", "FDA.gov", "PBI.gov" etc.
| `other` | Documents that cannot be confidently classified into any of the above |


### Classification guidelines

When multiple categories appear applicable, choose the document's **primary purpose**, not simply its filing form.

Examples:

- SEC Form 6-K containing quarterly earnings → `financial_results`
- SEC Form 6-K containing a management appointment → `management_change`
- 8-K announcing an acquisition → `mna_restructuring`
- Exchange filing announcing an AGM → `shareholder_meeting`
- Press release announcing quarterly earnings → `financial_results`
- Investor Day slide deck → `investor_presentation`
- Earnings webcast invitation → `earnings_call_update`

If no canonical type can be determined with reasonable confidence, use `other` and explicitly explain why the classification was ambiguous.

## Translation

Some sources require translation into English before embedding. The translation
pipeline is a separate service — the embedding pipeline simply waits for it.

### Columns on `documents`

```sql
-- Added to documents table:
ALTER TABLE documents
    ADD COLUMN translation_required  boolean NOT NULL DEFAULT false,
    ADD COLUMN translation_status    text    NOT NULL DEFAULT 'not_required',
    ADD COLUMN translated_ocr_path   text,
    ADD COLUMN detected_language     text,       -- e.g. 'fi', 'de', 'fr'
    ADD COLUMN language_confidence   numeric(4,3),
    ADD COLUMN translation_provider  text;       -- e.g. 'deepl', 'azure', 'google'

CREATE INDEX idx_documents_translation_pending
    ON documents (translation_required, translation_status)
    WHERE translation_required = true AND translation_status = 'pending';
```

| Column | Values | Set by |
|---|---|---|
| `translation_required` | `true` / `false` | Ingestion pipeline — never changed after |
| `translation_status` | `'not_required'` / `'pending'` / `'translating'` / `'translated'` / `'failed'` | Ingestion sets `'pending'`; translation pipeline owns all transitions after |
| `translated_ocr_path` | S3 URI of translated `.mmd` or NULL | Translation pipeline |
| `detected_language` | ISO 639-1 code, e.g. `'fi'` | Translation pipeline |
| `language_confidence` | 0.000–1.000 | Translation pipeline |
| `translation_provider` | `'deepl'`, `'azure'`, `'google'` etc. | Translation pipeline |

### `translation_status` values

| Value | Meaning |
|---|---|
| `'not_required'` | No translation needed. Default for all sources. Also used by the translation pipeline when it detects the document is already in English (even if `translation_required=true`). |
| `'pending'` | Queued for translation — set by the **ingestion pipeline** when `translation_required=true`. |
| `'translating'` | Claimed by a translation worker — prevents two workers processing the same row. |
| `'translated'` | Translation complete — `translated_ocr_path` is populated. |
| `'failed'` | Translation failed — see `processing_error`. |

### Lifecycle with translation

```
ocr_completed
  │
  ├─ translation_required=false, translation_status='not_required'
  │    → embedding picks up immediately
  │
  └─ translation_required=true, translation_status='pending'
       │
       ▼  [Translation pipeline claims → sets 'translating']
       │
       ├─ document is already English
       │    → set translation_status='not_required', detected_language='en'
       │    → embedding picks up (translation_status='not_required' is accepted)
       │
       └─ document needs translation
            → translate, upload to S3, set translated_ocr_path
            → set translation_status='translated', detected_language, provider
            → embedding picks up (uses translated_ocr_path)
```

### Ingestion pipeline responsibility

Set `translation_required=true` **and** `translation_status='pending'` together
for sources where non-English filings are expected. Both must be set atomically.

```python
# Nasdaq Helsinki — Finnish filings may need translation
documents_row["translation_required"] = True
documents_row["translation_status"]   = "pending"  # must be set — do not rely on default
```

**Critical:** if `translation_required=true` but `translation_status` is left
at its default `'not_required'`, the row becomes invisible to both the
translation pipeline (looks for `'pending'`) and the embedding pipeline (accepts
`'not_required'` and `'translated'` — so it WOULD embed, but without translation).
Always set both fields together.

For sources that never need translation:
```python
documents_row["translation_required"] = False
# translation_status defaults to 'not_required' — correct, no need to set it
```

### Embedding pipeline behaviour

The TEXT claim accepts documents where `translation_status IN ('not_required', 'translated')`.

- `translation_required=false, translation_status='not_required'` → ✓ embedded as-is
- `translation_required=true, translation_status='not_required'` → ✓ embedded as-is (translation pipeline confirmed English)
- `translation_required=true, translation_status='translated'` → ✓ embedded using `translated_ocr_path`
- `translation_required=true, translation_status='pending'` → ✗ waiting for translation pipeline
- `translation_required=true, translation_status='translating'` → ✗ in-progress
- `translation_required=true, translation_status='failed'` → ✗ needs manual intervention

`translation_required` is **never changed** after ingestion — it is a permanent
record of what the ingestion pipeline intended. The translation pipeline records
its conclusion in `translation_status` and `detected_language`.

### Translation pipeline responsibility (not in this repo)

1. **Claim** — `UPDATE documents SET translation_status='translating' WHERE translation_required=true AND translation_status='pending' ... FOR UPDATE SKIP LOCKED`
2. **Detect** — detect language of `ocr_path` content; write `detected_language`, `language_confidence`
3. **If English** — set `translation_status='not_required'`. Leave `translation_required=true` as the audit record that a check was performed.
4. **If non-English** — translate the `.mmd`, upload to S3, set `translated_ocr_path`, `translation_provider`, `translation_status='translated'`
5. **On failure** — set `translation_status='failed'`, write to `processing_error`

### Monitoring query — catch stalled rows

```sql
-- Rows that will never be embedded and never be translated:
-- translation_required=true but status still at default 'not_required'
-- (ingestion forgot to set 'pending')
SELECT id, source_table, doc_name, ingestion_status, translation_required, translation_status
FROM documents
WHERE translation_required = true
  AND translation_status   = 'not_required'
  AND ingestion_status NOT IN ('embedding_complete', 'ready_for_analytics', 'failed');
```

Run this periodically. Any rows returned indicate an ingestion pipeline bug.

### If translation is not yet available for a source

Set `translation_required=false` at ingestion time. Documents are embedded
as-is in their original language. **Warn the user** — search quality for
English queries against non-English content will be degraded until translation
is available.

---

## What NOT to do

- **Do not write to `documents.embeddings_completed_at`, `ready_for_search_at`,
  `indexed_in_vector`, `vector_db_doc_id`, or `retry_count`** from the ingestion
  pipeline. These are owned by the embedding pipeline.
- **Do not update `ingestion_status` past `'ocr_completed'`** from ingestion
  or the OCR runner. Everything after that is the embedding pipeline's
  responsibility.
- **Do not write embedding lifecycle state to the sidecar table.** The sidecar
  is ingestion-only. Embedding state lives on `documents`.
- **Do not add source-specific logic to the embedding pipeline or OCR runner.**
  If you find yourself needing to, the `documents` table is probably missing a
  field — add it there instead.

## OPENING A GPT CLASSIFICATION/TASK LOOP

The Agent can use the GPT-NANO (gpt-5-nano, cost: $0.05/Million Input tokens, $0.4/Million Output tokens) for various use cases, such as classification, document understand, etc. however, it is advised that these use cases should be offline, and not a runtime inference unless the USER says otherwise. To spawn the gpt-nano, use AZURE_OPENAI_API_KEY, with the appropriate AZURE endpoints/urls/ and deployment_name/model_name as "gpt-5-nano". 

The user would provide AZURE credentials in a .env file. In case AZURE credentials are missing/dont work/ mention this to the user, and they might provide an OPENAI credentials. 

The following code is how the gpt-nano is supposed to work:
<code>
import os
from dotenv import load_dotenv
load_dotenv(".env")

from openai import OpenAI

endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
api_version = os.environ["AZURE_OPENAI_API_VERSION"]
key = os.environ["AZURE_OPENAI_API_KEY"]

base_url = f"{endpoint}/openai/deployments/gpt-5-nano"
print("base_url host:", base_url.split("//")[1].split("/")[0])

client = OpenAI(
    api_key=key,
    base_url=base_url,
    default_query={"api-version": api_version},
)

resp = client.chat.completions.create(
    model="gpt-5-nano",
    messages=[{"role": "user", "content": "hi"}],
)
print(resp.choices[0].message.content)
</code>

Once you get a "hi" back from gpt-nano, you can proceed to use it. Mention the use case to the user first, and seek permissions. Also mention an estimated cost, which would usually be very small, but in case of large classification/ongoing tasks, a cost estimate is needed.


## NOTE:
#1: The new_ingestion_guidelines.md define the technical pipeline contract. 

#2: The new_stock_exchange_guidelines.md adds the stock-exchange specific policy.

#3: "OCR" means conversion of an initial, raw source into machine readable, embedding friendly data. "Who performs the OCR" depends on the "raw source", if the raw source itself is plain text/.xml/.html or any machine readable source, then the ingestion worker should have a small "OCR" module. And if the raw source is anything of the form of PDF, PPT, WORD, IMAGE, or etc., then the OCR RUNNER would perform the "OCR", and the ingestion worker would only ingest the document.