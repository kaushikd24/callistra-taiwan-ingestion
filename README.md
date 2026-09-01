# Taiwan eMOPS ingestion

Hourly ingestion of English TWSE/eMOPS Material Information. It fetches the
official current-day HTML list, retrieves every detail record, writes original
HTML plus embedding-ready `.mmd` to S3, and creates matching Postgres sidecar
and `documents` rows. It does not use the translation pipeline:
`translation_required=false`.

## Prerequisites

1. Apply [the sidecar migration](migrations/001_create_twse_emops_documents.sql)
   once to Callistra Postgres.
2. Set `TWSE_MOPS_S3_BUCKET` to the approved bucket. Do not use the sample
   value without confirming the bucket exists.
3. Provide the Cloud SQL and AWS variables shown in `.env.example`.

## Run

```bash
pip install -r requirements.txt

# Read-only verification: list, pagination, detail HTML, MMD conversion and classification.
python -m twse_emops.worker --once --dry-run --max-documents 2

# One live ingestion cycle.
python -m twse_emops.worker --once

# Worker mode (one cycle per hour by default).
python -m twse_emops.worker
```

The live worker only polls the current Taiwan day; it does not backfill.
It deduplicates on the source-native key:
`company_code:YYYYMMDD:HHMMSS:sequence_no`.

## Storage and pipeline state

For a source item such as `2330:20260901:123456:1`, the worker writes:

```text
s3://<bucket>/twse_emops/2026/09/01/2330_123456_1.html
s3://<bucket>/twse_emops/2026/09/01/2330_123456_1.mmd
```

`documents.blob_path` points to the original HTML and `documents.ocr_path` to
the generated MMD. The document is inserted as `ingestion_status='ocr_completed'`,
so the OCR runner is skipped and embedding can proceed.

## Deployment

This is an outbound-only Dokku worker. The repo includes `Dockerfile`,
`Procfile`, `.dockerignore`, and `start.sh`. Deploy after the live one-cycle
test has verified both S3 objects and the two database records. Configure only
the AWS, Cloud SQL, and `TWSE_MOPS_S3_BUCKET` variables required by this repo,
then scale the Dokku `worker` process to one instance.
