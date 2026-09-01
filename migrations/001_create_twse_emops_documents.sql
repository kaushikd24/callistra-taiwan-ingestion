-- Apply once to Callistra Postgres before enabling the worker.
CREATE TABLE IF NOT EXISTS twse_emops_documents (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id             uuid UNIQUE REFERENCES documents(id),
    native_key              text NOT NULL UNIQUE,
    company_code            text NOT NULL,
    company_name            text,
    market                  text NOT NULL,
    announcement_date       date NOT NULL,
    announcement_time       time NOT NULL,
    sequence_no             integer NOT NULL,
    subject                 text NOT NULL,
    detail_endpoint         text NOT NULL,
    detail_request          jsonb NOT NULL,
    raw_list_row            jsonb NOT NULL,
    detail_html_blob_path   text NOT NULL,
    mmd_blob_path           text NOT NULL,
    fetched_at              timestamptz NOT NULL DEFAULT now(),
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_twse_emops_documents_published
    ON twse_emops_documents (announcement_date DESC, announcement_time DESC);
