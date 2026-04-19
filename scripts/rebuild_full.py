#!/usr/bin/env python3
"""Rebuild the japan-ir-search index for 1 year with force re-download.

Usage:
    EDINET_API_KEY=... uv run python3 scripts/rebuild_full.py [--years N]

Progress is logged to /tmp/rebuild_full.log
"""
import asyncio
import logging
import os
import sys
import time
from datetime import date, timedelta

# Ensure the package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from japan_ir_search.builder import build_index_for_date
from japan_ir_search.config import INDEXABLE_DOC_TYPES_ALL
from japan_ir_search.edinet_client import EdinetClient
from japan_ir_search.index import SearchIndex

LOG_FILE = "/tmp/rebuild_full.log"


def _reset_fts_to_contentless(db_path: str) -> None:
    """Drop old content-bearing FTS table and recreate as contentless."""
    import sqlite3

    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        # Check if FTS table exists and is content-bearing (old v1 format)
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='filings_fts'"
        ).fetchone()
        if row and "content=''" not in row[0]:
            print("Resetting FTS table from content-bearing to contentless...", flush=True)
            conn.execute("DROP TABLE IF EXISTS filings_fts")
            conn.execute("""
                CREATE VIRTUAL TABLE filings_fts USING fts5(
                    doc_id UNINDEXED,
                    company_name,
                    section_key UNINDEXED,
                    section_text,
                    content='',
                    tokenize='trigram'
                )
            """)
            # Re-populate FTS from existing data
            conn.execute(
                "INSERT INTO filings_fts (rowid, doc_id, company_name, section_key, section_text) "
                "SELECT fs.rowid, fs.doc_id, f.company_name, fs.section_key, fs.section_text "
                "FROM filing_sections fs JOIN filings f ON f.doc_id = fs.doc_id"
            )
            conn.commit()
            print("FTS reset complete.", flush=True)
    finally:
        conn.close()


logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def log(msg: str) -> None:
    """Print + log a message."""
    print(msg, flush=True)
    logger.info(msg)


async def main() -> None:
    years = 1
    if len(sys.argv) > 1 and sys.argv[1] == "--years":
        years = int(sys.argv[2])

    api_key = os.environ.get("EDINET_API_KEY")
    if not api_key:
        print("ERROR: Set EDINET_API_KEY environment variable", flush=True)
        sys.exit(1)

    db_path = os.path.expanduser("~/.japan-ir-search/filings.db")

    # For force rebuild: reset the FTS table to contentless mode
    # (old DBs may have content-bearing FTS which causes SQL logic errors)
    _reset_fts_to_contentless(db_path)

    index = SearchIndex()
    index.initialize()

    end_date = date.today()
    start_date = end_date - timedelta(days=365 * years)
    total_days = (end_date - start_date).days
    weekdays = sum(
        1 for d in range(total_days + 1)
        if (start_date + timedelta(days=d)).weekday() < 5
    )

    log(f"=== Rebuild Start ===")
    log(f"Period: {start_date} ~ {end_date} ({total_days} days, {weekdays} weekdays)")
    log(f"Mode: force=True (re-download all)")
    log(f"Log: {LOG_FILE}")
    log("")

    total_indexed = 0
    total_errors = 0
    total_no_html = 0
    total_skipped = 0
    days_done = 0
    t_start = time.monotonic()

    async with EdinetClient(api_key) as client:
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:  # Skip weekends
                date_str = current.isoformat()
                days_done += 1
                try:
                    result = await build_index_for_date(
                        client, index, date_str,
                        target_types=INDEXABLE_DOC_TYPES_ALL,
                        force=True,
                    )
                    total_indexed += result["indexed"]
                    total_errors += result["errors"]
                    total_no_html += result["no_html"]
                    total_skipped += result["skipped"]

                    elapsed = time.monotonic() - t_start
                    pct = days_done / weekdays * 100
                    eta_s = (elapsed / days_done) * (weekdays - days_done) if days_done > 0 else 0
                    eta_h = eta_s / 3600

                    if result["indexed"] > 0 or result["errors"] > 0:
                        log(
                            f"[{pct:5.1f}%] {date_str}: "
                            f"indexed={result['indexed']} no_html={result['no_html']} "
                            f"errors={result['errors']} target={result['docs_target']} "
                            f"(ETA: {eta_h:.1f}h)"
                        )
                    elif days_done % 20 == 0:
                        log(f"[{pct:5.1f}%] {date_str}: no target docs (ETA: {eta_h:.1f}h)")

                except Exception as e:
                    total_errors += 1
                    log(f"[ERROR] {date_str}: {e}")

            current += timedelta(days=1)

    elapsed_total = time.monotonic() - t_start
    hours = elapsed_total / 3600

    log("")
    log(f"=== Rebuild Complete ===")
    log(f"  Duration:     {hours:.1f} hours ({elapsed_total:.0f}s)")
    log(f"  Days done:    {days_done}/{weekdays}")
    log(f"  Indexed:      {total_indexed:,}")
    log(f"  No HTML:      {total_no_html:,}")
    log(f"  Errors:       {total_errors:,}")
    log(f"  Skipped:      {total_skipped:,}")

    # Quality check
    import sqlite3
    conn = sqlite3.connect(f"file://{index._db_path}?mode=ro", uri=True)
    total_filings = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
    total_120 = conn.execute("SELECT COUNT(*) FROM filings WHERE doc_type_code='120'").fetchone()[0]
    with_sections = conn.execute("""
        SELECT COUNT(DISTINCT f.doc_id) FROM filings f
        JOIN filing_sections fs ON f.doc_id = fs.doc_id
        WHERE f.doc_type_code='120' AND fs.section_key != 'full_text'
    """).fetchone()[0]
    date_range = conn.execute("SELECT MIN(filing_date), MAX(filing_date) FROM filings").fetchone()
    conn.close()

    log("")
    log(f"=== Quality Check ===")
    log(f"  Total filings: {total_filings:,}")
    log(f"  Date range: {date_range[0]} ~ {date_range[1]}")
    log(f"  有報(120): {total_120}, with sections: {with_sections} ({with_sections/total_120*100:.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
