# Taiwan MOPS research notes

Research date: 2026-09-01

## Company universe

`https://isin.twse.com.tw/isin/C_public.jsp?strMode=2` is an official TWSE
ISIN directory encoded as CP950/Big5. It is a single large, legacy HTML table,
not an API. It contains a `股票` (stocks) section followed by warrants and other
instruments. The generated `output/twse_listed_companies.csv` intentionally
includes only the 1,054 rows in the stocks section.

Fields retained are ticker, Chinese issuer name, ISIN, listing date, listed
status, TWSE industry, CFI code, and notes. English issuer names are matched by
the supplied `stockanalysis_master_list.csv` on `(country=Taiwan,
exchange_id=TPE, symbol=ticker)`; GICS fields come from the supplied Callistra
company mapping, matching ISIN first. The result resolves all 1,054 names;
the one name absent from the supplied universe is a small, documented curated
exception (`7855`, HOTAI LEASING CO., LTD.), rather than an LLM translation.

`scripts/build_twse_company_directory.py` streams the page and stops at the
end of `股票`; this avoids downloading the much larger warrants section and is
robust to the source's slow chunked response.

## Three relevant announcement views

### 1. English Material Information (eMOPS)

The English site is `https://emops.twse.com.tw/server-java/t58query`. It is a
legacy server-rendered site. The relevant detailed records use the
`t05st01_e` *Material Information* template: issuer, sequence number,
announcement date/time, unstructured subject, event date, a "To which item it
meets" paragraph, and a structured HTML statement.

Treat this as an English corporate-material-information feed. It needs no
translation, but its subject is not a filing type; classification must use the
full document (and the paragraph number when present), not only the subject.

The legacy navigation endpoint `t58main` returned the official security
message when accessed directly, but the supplied HAR established the reliable
daily list contract. No cookies were required in reproduction:

```text
POST https://emops.twse.com.tw/server-java/t58query
Content-Type: application/x-www-form-urlencoded

step=41&caption_id=000001&co_id=2330&ifrs=&TYPEK=&YEAR=&pagenum=1
```

The endpoint returns all markets' current-day English material information in
HTML (15 rows per page; the test day had nine pages). `co_id` is present in the
legacy form but does not filter this daily list. Parse each row's `view`
button, which contains the full detail request:

```text
POST https://emops.twse.com.tw/server-java/t05sr01_1_e

co_id=<code>&spoke_date=<YYYYMMDD>&spoke_time=<HHMMSS>&seq_no=<n>&isNew=Y
```

This directly returns the popup's English HTML and was reproduced without a
browser session. The unique native key is therefore
`(co_id, spoke_date, spoke_time, seq_no)`; retain the endpoint/body as source
metadata rather than relying on a constructed GET URL.

### 2. Chinese Material Information (the likely language counterpart)

The Chinese MOPS `t05st01` Material Information module is the likely
Chinese-language counterpart of eMOPS `t05sr01_1_e`: both are the broad
"Material Information" disclosure family, with an issuer/date/time/sequence
identity and a detailed statement. This is **not** the `t146sb10` page below.
It was not selected as the first Chinese implementation path because it adds
translation work without expanding the English corpus; its exact live-list
contract should be captured if we decide to use it.

### 3. Chinese statutory announcement query (modern MOPS)

`https://mops.twse.com.tw/mops/#/web/t146sb10` is a Vue SPA with a first-party
JSON endpoint:

```text
POST https://mops.twse.com.tw/mops/api/t146sb10
Content-Type: application/json
```

For example, a valid company/date query is:

```json
{
  "scopeType": "1",
  "companyId": "2330",
  "announcementBasis": "0",
  "dateType": "1",
  "firstDate": "1140101",
  "lastDate": "1141231",
  "announcementType": "1",
  "sort": "2"
}
```

Dates are ROC dates (`Gregorian year - 1911`). `scopeType: "2"` plus
`marketKind: "sii"` queries the listed market. The response has grouped rows
and signed detail URLs on `mopsov.twse.com.tw`; those URLs return the detailed
Chinese HTML. A test for TSMC in 2025 returned 198 records across statutory
groups, demonstrating that the endpoint is available without cookies or a JS
challenge.

The 34 UI `announcementType` values are a controlled statutory vocabulary
(e.g. asset acquisition/disposal, private-placement securities, shareholder
meetings, dividends, financing, treasury stock, accounting changes). That is
the right source-side key for a static canonical-type map.

## Why the feeds do not match one-for-one

The English Material Information feed and **Chinese `t146sb10`** are not
language mirrors. `t05sr01_1_e` is a broad Material Information publication
template; `t146sb10` is a separate, structured statutory-announcement query.
For example, First Steamship's English Material Information capital-reduction
record (code 2601, 2026-09-01 18:34:33, paragraph 36) did not appear in a
same-issuer/same-day `t146sb10` query. Some economic events can still overlap,
but rows differ by scope, timing, and legal form. Do not attempt a mandatory
Chinese-to-English match, and do not discard unmatched rows. Ingest these as
separate source feeds and only de-duplicate exact source-native documents.

## Ingestion recommendation

1. Use MOPS `t146sb10` as the first production candidate. Poll `marketKind=sii`
   hourly, with a Monday-of-current-week cutoff; query per date if the API has
   no pagination contract. De-duplicate on the signed detail URL hash plus
   `(company_id, announcement_date, sequence_no, announcement_type)`.
2. Put source metadata in a `twse_mops_documents` sidecar table, including the
   controlled `announcement_type`, full API row, source detail URL, native
   announcement date, and source sequence number. Write it and `documents` in
   one transaction.
3. Save both the original detail HTML and a generated embedding-ready `.mmd`
   to S3. The frontend needs the original HTML; the pipeline needs the `.mmd`.
   For this machine-readable feed, set `ingestion_status='ocr_completed'` and
   `ocr_path` to the `.mmd`.
4. Set `translation_required=true` and `translation_status='pending'` for
   Chinese MOPS documents. The established translation service must create a
   translated `.mmd` before embedding. For English eMOPS documents, set
   `translation_required=false`.

Suggested type mapping for t146:

| Announcement themes | Canonical type |
|---|---|
| shareholder meeting, director-candidate/proposal | `shareholder_meeting` |
| dividends, listing/delisting, new shares, convertibles, treasury shares, paperless conversion | `corporate_action` |
| private placement / overseas bonds | `fundraising` |
| acquisition/disposal of assets; merger, share conversion, split | `mna_restructuring` |
| CPA/accounting-officer changes | `management_change` |
| financial-report declaration, internal controls, ownership disclosures, statutory notices/accounting change | `regulatory_compliance` |
| loans, guarantees, residual company-law notices | `general_disclosure` |

## Translation decision

Do not introduce a local translation model for the company directory: it is
reference data and is almost completely covered by the supplied English
universe, with a fixed translation map for its 32 industry labels. An LLM would
make this less reproducible.

"GPT Nano" is an API model, not a model that can run locally. A local
HuggingFace option such as NLLB-200 is technically possible, but it is a poor
first production choice for long, table-heavy financial disclosures: it adds a
multi-GB model/runtime and still requires terminology and table-preservation
validation. Use the existing document translation pipeline/provider contract;
only consider a local model later as an evaluated fallback.

## Bot-protection assessment

MOPS JSON API: level 1 (no challenge seen; normal JSON POST works). The ISIN
directory: level 1, but operationally fragile because of CP950 and a large,
slow chunked response. eMOPS Material Information: level 1; daily list and
detail HTML POSTs work without cookies. Direct `t58main` navigation is blocked,
but it is not needed by the worker.
