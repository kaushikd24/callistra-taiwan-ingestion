#!/usr/bin/env python3
"""Hourly worker for the English eMOPS Material Information feed."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import boto3
import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from analytics_db.db import get_analytics_db  # noqa: E402

LIST_URL = "https://emops.twse.com.tw/server-java/t58query"
DETAIL_URL = "https://emops.twse.com.tw/server-java/t05sr01_1_e"
TAIPEI = ZoneInfo("Asia/Taipei")
USER_AGENT = "Callistra Taiwan filings ingestion/1.0 (+https://callistra.ai)"


@dataclass(frozen=True)
class Announcement:
    market: str
    company_code: str
    company_name: str
    announced_on: date
    announced_at: datetime_time
    sequence_no: int
    subject: str

    @property
    def native_key(self) -> str:
        return f"{self.company_code}:{self.announced_on:%Y%m%d}:{self.announced_at:%H%M%S}:{self.sequence_no}"

    @property
    def detail_form(self) -> dict[str, str]:
        return {
            "co_id": self.company_code,
            "spoke_date": self.announced_on.strftime("%Y%m%d"),
            "spoke_time": self.announced_at.strftime("%H%M%S"),
            "seq_no": str(self.sequence_no),
            "isNew": "Y",
        }


def request_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return session


def _text(cell: Tag) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def parse_list_page(source_html: str) -> tuple[list[Announcement], int]:
    soup = BeautifulSoup(source_html, "html.parser")
    table = next((table for table in soup.find_all("table") if "Announcement" in table.get_text(" ", strip=True)), None)
    if table is None:
        raise ValueError("eMOPS list response did not contain the announcement table")
    rows: list[Announcement] = []
    for row in table.find_all("tr")[2:]:
        cells = row.find_all("td", recursive=False)
        if len(cells) != 7:
            continue
        button = cells[6].find("button")
        onclick = button.get("onclick", "") if button else ""
        values = dict(re.findall(r"\.([a-z_]+)\.value='([^']*)'", onclick))
        try:
            rows.append(Announcement(
                market=_text(cells[0]), company_code=values["co_id"], company_name=_text(cells[2]),
                announced_on=datetime.strptime(values["spoke_date"], "%Y%m%d").date(),
                announced_at=datetime.strptime(values["spoke_time"], "%H%M%S").time(),
                sequence_no=int(values["seq_no"]), subject=_text(cells[5]),
            ))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Could not parse eMOPS row: {row}") from exc
    page_links = [int(value) for value in re.findall(r"pagenum\.value='(\d+)'", source_html)]
    return rows, max(page_links, default=1)


def fetch_daily_announcements(session: requests.Session) -> list[Announcement]:
    all_rows: dict[str, Announcement] = {}
    def get_page(page: int) -> tuple[list[Announcement], int]:
        error: Exception | None = None
        for attempt in range(3):
            try:
                response = session.post(
                    LIST_URL,
                    data={"step": "41", "caption_id": "000001", "pagenum": str(page)},
                    timeout=45,
                )
                response.raise_for_status()
                return parse_list_page(response.text)
            except (requests.RequestException, ValueError) as exc:
                error = exc
                if attempt < 2:
                    delay = 1 + attempt * 2
                    logging.warning("eMOPS list page %d failed (%s); retrying in %ss", page, exc, delay)
                    time.sleep(delay)
        raise RuntimeError(f"eMOPS list page {page} failed after three attempts") from error

    first_rows, last_page = get_page(1)
    for row in first_rows:
        all_rows[row.native_key] = row
    # Keep the legacy portal's request rate deliberately low. Retries above
    # cover intermittent HTML error pages observed during repeated polling.
    workers = int(os.getenv("TWSE_MOPS_PAGE_WORKERS", "2"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows, _ in pool.map(get_page, range(2, last_page + 1)):
            for row in rows:
                all_rows[row.native_key] = row
    return sorted(all_rows.values(), key=lambda item: item.native_key)


def fetch_detail_html(session: requests.Session, item: Announcement) -> str:
    response = session.post(DETAIL_URL, data=item.detail_form, timeout=45)
    response.raise_for_status()
    if "Material Information" not in response.text and "Today's Information" not in response.text:
        raise ValueError(f"Unexpected eMOPS detail response for {item.native_key}")
    return response.text


def canonical_doc_type(subject: str, detail_html: str) -> str:
    text = f"{subject} {BeautifulSoup(detail_html, 'html.parser').get_text(' ', strip=True)}".lower()
    if re.search(r"shareholders?[’']? meeting|annual general meeting|extraordinary.*meeting", text):
        return "shareholder_meeting"
    if re.search(r"acquisition|disposal|merger|spin.?off|business combination|transfer.*shares", text):
        return "mna_restructuring"
    if re.search(r"private placement|capital increase|cash capital|corporate bonds?|convertible bonds?|fund raising", text):
        return "fundraising"
    if re.search(r"dividend|capital reduction|ex-dividend|ex-rights|stock split|treasury shares?|repurchase", text):
        return "corporate_action"
    if re.search(r"chairman|chief executive|ceo|president|spokesperson|director.*resign|appointment", text):
        return "management_change"
    if re.search(r"revenue report|financial report|financial statements?|earnings|income statement", text):
        return "financial_results"
    if re.search(r"investor conference|earnings call|conference call|webcast", text):
        return "earnings_call_update"
    if re.search(r"internal control|accounting|auditor|compliance|violation|regulatory", text):
        return "regulatory_compliance"
    return "general_disclosure"


def html_to_mmd(source_html: str) -> str:
    """Convert eMOPS detail HTML to the required single-page MMD representation."""
    soup = BeautifulSoup(source_html, "html.parser")
    for tag in soup(["script", "style", "button", "form"]):
        tag.decompose()
    blocks = ["## Page 1"]
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "pre", "table"]):
        if tag.name == "table":
            table_rows = []
            for row in tag.find_all("tr"):
                values = [_text(cell).replace("|", "\\|") for cell in row.find_all(["th", "td"])]
                if values:
                    table_rows.append(values)
            if table_rows:
                width = max(len(row) for row in table_rows)
                padded = [row + [""] * (width - len(row)) for row in table_rows]
                blocks.append("| " + " | ".join(padded[0]) + " |")
                blocks.append("| " + " | ".join(["---"] * width) + " |")
                blocks.extend("| " + " | ".join(row) + " |" for row in padded[1:])
                blocks.append(str(tag))
        else:
            value = _text(tag)
            if value:
                prefix = "### " if tag.name in {"h1", "h2", "h3", "h4"} else ""
                blocks.append(prefix + value)
    return "\n\n".join(blocks).strip() + "\n"


class Storage:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self.client = boto3.client("s3")

    def put(self, key: str, content: str, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content.encode("utf-8"), ContentType=content_type)
        return f"s3://{self.bucket}/{key}"


def object_keys(item: Announcement) -> tuple[str, str]:
    prefix = f"twse_emops/{item.announced_on:%Y/%m/%d}/{item.company_code}_{item.announced_at:%H%M%S}_{item.sequence_no}"
    return f"{prefix}.html", f"{prefix}.mmd"


def exists_in_database(native_key: str) -> bool:
    return get_analytics_db().query_one("SELECT 1 AS found FROM twse_emops_documents WHERE native_key = %s", (native_key,)) is not None


def persist(item: Announcement, detail_html: str, mmd: str, raw_html_path: str, mmd_path: str, document_type: str) -> bool:
    db = get_analytics_db()
    published_at = datetime.combine(item.announced_on, item.announced_at, tzinfo=TAIPEI)
    metadata = json.dumps({"native_key": item.native_key, "detail_endpoint": DETAIL_URL, "detail_request": item.detail_form})
    with db.connection() as conn:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO twse_emops_documents
                    (native_key, company_code, company_name, market, announcement_date, announcement_time, sequence_no,
                     subject, detail_endpoint, detail_request, raw_list_row, detail_html_blob_path, mmd_blob_path)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                ON CONFLICT (native_key) DO NOTHING
                RETURNING id
            """, (item.native_key, item.company_code, item.company_name, item.market, item.announced_on, item.announced_at,
                  item.sequence_no, item.subject, DETAIL_URL, json.dumps(item.detail_form), json.dumps(asdict(item), default=str), raw_html_path, mmd_path))
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return False
            sidecar_id = row[0]
            cur.execute("""
                INSERT INTO documents
                    (source_table, source_row_id, source_system, source_type, doc_name, title, canonical_doc_type,
                     raw_category, entity_type, primary_symbol, symbols, exchange, country, country_code, company_name,
                     published_at, blob_path, ocr_path, ingestion_status, translation_required, translation_status, metadata)
                VALUES
                    ('twse_emops_documents', %s, 'twse_emops', 'regulatory_filing', %s, %s, %s,
                     'material_information', 'company', %s, %s, 'TWSE', 'TAIWAN', 'TW', %s,
                     %s, %s, %s, 'ocr_completed', false, 'not_required', %s::jsonb)
                RETURNING id
            """, (sidecar_id, f"{item.native_key}.html", item.subject, document_type, item.company_code,
                  [item.company_code], item.company_name, published_at, raw_html_path, mmd_path, metadata))
            document_id = cur.fetchone()[0]
            cur.execute("UPDATE twse_emops_documents SET document_id = %s, updated_at = now() WHERE id = %s", (document_id, sidecar_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def run_once(*, dry_run: bool, max_documents: int | None = None) -> tuple[int, int]:
    session = request_session()
    items = fetch_daily_announcements(session)
    logging.info("Fetched %d English eMOPS announcement rows.", len(items))
    storage = None if dry_run else Storage(os.environ["TWSE_MOPS_S3_BUCKET"])
    created = skipped = 0
    for item in items[:max_documents]:
        if not dry_run and exists_in_database(item.native_key):
            skipped += 1
            continue
        detail_html = fetch_detail_html(session, item)
        mmd = html_to_mmd(detail_html)
        doc_type = canonical_doc_type(item.subject, detail_html)
        if dry_run:
            logging.info("Would ingest %s as %s", item.native_key, doc_type)
            created += 1
            continue
        html_key, mmd_key = object_keys(item)
        raw_html_path = storage.put(html_key, detail_html, "text/html; charset=utf-8")
        mmd_path = storage.put(mmd_key, mmd, "text/markdown; charset=utf-8")
        if persist(item, detail_html, mmd, raw_html_path, mmd_path, doc_type):
            created += 1
        else:
            skipped += 1
    return created, skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one poll then exit.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse without S3 or Postgres writes.")
    parser.add_argument("--max-documents", type=int, help="Cap documents per cycle; intended for testing only.")
    parser.add_argument("--poll-interval", type=int, default=3600)
    args = parser.parse_args()
    if not args.dry_run and not os.getenv("TWSE_MOPS_S3_BUCKET"):
        parser.error("TWSE_MOPS_S3_BUCKET must be set for a live run.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            created, skipped = run_once(dry_run=args.dry_run, max_documents=args.max_documents)
            logging.info("Cycle complete: created=%d skipped=%d", created, skipped)
        except Exception:
            logging.exception("Cycle failed")
        if args.once:
            return
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
