#!/usr/bin/env python3
"""Build a clean English-enriched TWSE listed-company directory from TWSE ISIN.

The source is Big5/CP950 HTML and contains many non-equity instruments.  This
streaming parser deliberately stops after the ``股票`` (stocks) section, before
the warrant section, so it does not depend on the legacy server closing its
multi-megabyte chunked response promptly.
"""
from __future__ import annotations

import argparse
import codecs
import csv
import re
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

import requests


SOURCE_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"

INDUSTRY_EN = {
    "水泥工業": "Cement", "食品工業": "Food", "塑膠工業": "Plastics",
    "建材營造業": "Building Materials and Construction", "汽車工業": "Automotive",
    "其他業": "Other", "紡織纖維": "Textiles", "運動休閒": "Sports and Leisure",
    "電子零組件業": "Electronic Components", "電機機械": "Electrical Machinery",
    "電器電纜": "Electrical Cable", "生技醫療業": "Biotechnology and Healthcare",
    "化學工業": "Chemicals", "玻璃陶瓷": "Glass and Ceramics", "造紙工業": "Paper",
    "鋼鐵工業": "Steel", "居家生活": "Home Living", "綠能環保": "Green Energy and Environmental Services",
    "橡膠工業": "Rubber", "航運業": "Shipping", "電腦及週邊設備業": "Computers and Peripherals",
    "半導體業": "Semiconductors", "其他電子業": "Other Electronics", "通信網路業": "Communications and Internet",
    "光電業": "Optoelectronics", "電子通路業": "Electronic Distribution", "資訊服務業": "Information Services",
    "油電燃氣業": "Oil, Gas and Electricity", "觀光餐旅": "Tourism and Hospitality",
    "金融保險業": "Financial Services and Insurance", "貿易百貨業": "Trading and Retail",
    "數位雲端": "Digital and Cloud Services",
}

# Kept deliberately tiny: only names absent from the supplied English universe.
CURATED_ENGLISH_NAMES = {"7855": "HOTAI LEASING CO., LTD."}


class StopAfterStocks(Exception):
    """The next ISIN section was reached; no more common-stock rows exist."""


class IsinStockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.cells: list[str] = []
        self.cell_parts: list[str] = []
        self.category: str | None = None
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self.in_row, self.cells = True, []
        elif self.in_row and tag == "td":
            self.in_cell, self.cell_parts = True, []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.in_cell:
            self.cells.append(" ".join("".join(self.cell_parts).split()))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if len(self.cells) == 1:
                new_category = self.cells[0]
                if self.category == "股票" and new_category != "股票":
                    raise StopAfterStocks
                self.category = new_category
            elif self.category == "股票" and len(self.cells) == 7:
                self.rows.append(self.cells)


def stream_stock_rows(url: str) -> list[list[str]]:
    headers = {"User-Agent": "Callistra Taiwan ingestion research (+https://callistra.ai)"}
    parser = IsinStockParser()
    decoder = codecs.getincrementaldecoder("cp950")("strict")
    with requests.get(url, headers=headers, stream=True, timeout=(15, 90)) as response:
        response.raise_for_status()
        try:
            for chunk in response.iter_content(chunk_size=32_768):
                if chunk:
                    parser.feed(decoder.decode(chunk))
        except StopAfterStocks:
            pass
    return parser.rows


def read_csv(path: Path) -> Iterable[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def load_enrichment(company_map: Path, stockanalysis_map: Path):
    callistra_by_isin: dict[str, dict[str, str]] = {}
    callistra_by_ticker: dict[str, dict[str, str]] = {}
    for row in read_csv(company_map):
        if row.get("country_code") == "TW":
            callistra_by_isin[row["isin"]] = row
            callistra_by_ticker[row["ticker"]] = row

    english_by_ticker: dict[str, list[str]] = defaultdict(list)
    for row in read_csv(stockanalysis_map):
        if row.get("country_name") == "Taiwan" and row.get("exchange_id") == "TPE":
            english_by_ticker[row["symbol"]].append(row["company_name"])
    return callistra_by_isin, callistra_by_ticker, english_by_ticker


def build_rows(rows, company_map: Path, stockanalysis_map: Path):
    by_isin, by_ticker, english_by_ticker = load_enrichment(company_map, stockanalysis_map)
    output = []
    for security, isin, listing_date, status_zh, industry_zh, cfi_code, notes in rows:
        match = re.match(r"^(\S+)\s+(.+)$", security)
        if not match:
            continue
        ticker, company_name_zh = match.groups()
        callistra = by_isin.get(isin) or by_ticker.get(ticker, {})
        english_candidates = english_by_ticker.get(ticker, [])
        curated_name = CURATED_ENGLISH_NAMES.get(ticker, "")
        english_name = english_candidates[0] if len(english_candidates) == 1 else curated_name
        output.append({
            "ticker": ticker,
            "company_name_en": english_name,
            "company_name_zh": company_name_zh,
            "isin": isin,
            "listing_date": listing_date,
            "exchange": "TWSE",
            "market_status_en": {"上市": "Listed"}.get(status_zh, status_zh),
            "market_status_zh": status_zh,
            "industry_en": INDUSTRY_EN.get(industry_zh, ""),
            "industry_zh": industry_zh,
            "cfi_code": cfi_code,
            "source_notes": notes,
            "gics_sector_code": callistra.get("gics_sector_code", ""),
            "gics_sector_name": callistra.get("gics_sector_name", ""),
            "gics_industry_code": callistra.get("gics_industry_code", ""),
            "gics_industry_name": callistra.get("gics_industry_name", ""),
            "name_translation_source": (
                "stockanalysis_master_list.csv" if len(english_candidates) == 1
                else "curated_official_name"
            ),
        })
    return output


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=root / "output" / "twse_listed_companies.csv")
    parser.add_argument("--company-map", type=Path, default=root / "company_mapping_full_with_gics_no_id.csv")
    parser.add_argument("--stockanalysis-map", type=Path, default=root / "stockanalysis_master_list.csv")
    args = parser.parse_args()

    result = build_rows(stream_stock_rows(SOURCE_URL), args.company_map, args.stockanalysis_map)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)
    translated = sum(bool(row["company_name_en"]) for row in result)
    print(f"Wrote {len(result)} TWSE stock rows to {args.output} ({translated} matched English names).")


if __name__ == "__main__":
    main()
