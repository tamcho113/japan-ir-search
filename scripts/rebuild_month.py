#!/usr/bin/env python3
"""Rebuild filings for a date range with force=True."""
import asyncio
import logging
import sqlite3
import sys
import time
from datetime import date, timedelta

from japan_ir_search.builder import build_index_for_date
from japan_ir_search.config import INDEXABLE_DOC_TYPES_ALL
from japan_ir_search.edinet_client import EdinetClient
from japan_ir_search.index import SearchIndex


logging.basicConfig(level=logging.WARNING)

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30


async def main():
    index = SearchIndex()
    index.initialize()

    end = date.today()
    start = end - timedelta(days=DAYS)
    current = start

    total_indexed = 0
    total_errors = 0
    total_no_html = 0
    days_done = 0
    t_start = time.monotonic()

    sys.stdout.write(f"Rebuilding {start} ~ {end} ({DAYS} days, force=True)\n\n")
    sys.stdout.flush()

    async with EdinetClient() as client:
        while current <= end:
            if current.weekday() < 5:
                date_str = current.isoformat()
                try:
                    result = await build_index_for_date(
                        client, index, date_str,
                        target_types=INDEXABLE_DOC_TYPES_ALL,
                        force=True,
                    )
                    indexed = result["indexed"]
                    total_indexed += indexed
                    total_errors += result["errors"]
                    total_no_html += result["no_html"]
                    days_done += 1
                    elapsed = time.monotonic() - t_start
                    sys.stdout.write(
                        f"  {date_str}: indexed={indexed} no_html={result['no_html']} "
                        f"errors={result['errors']} target={result['docs_target']} "
                        f"[{elapsed:.0f}s elapsed]\n"
                    )
                    sys.stdout.flush()
                except Exception as e:
                    total_errors += 1
                    sys.stdout.write(f"  {date_str}: ERROR - {e}\n")
                    sys.stdout.flush()
            current += timedelta(days=1)

    elapsed_total = time.monotonic() - t_start
    sys.stdout.write(f"\n=== Summary ===\n")
    sys.stdout.write(f"  Days processed: {days_done}\n")
    sys.stdout.write(f"  Total indexed:  {total_indexed}\n")
    sys.stdout.write(f"  Total no_html:  {total_no_html}\n")
    sys.stdout.write(f"  Total errors:   {total_errors}\n")
    sys.stdout.write(f"  Elapsed:        {elapsed_total:.0f}s\n")
    sys.stdout.flush()

    # Quality check
    conn = sqlite3.connect(f"file://{index._db_path}?mode=ro", uri=True)
    recent = conn.execute(
        """SELECT f.doc_id, f.company_name, f.doc_type_code, f.filing_date,
                  COUNT(CASE WHEN fs.section_key != 'full_text' THEN 1 END) as section_count,
                  SUM(CASE WHEN fs.section_key = 'full_text' THEN fs.char_count ELSE 0 END) as ft_chars
           FROM filings f
           JOIN filing_sections fs ON f.doc_id = fs.doc_id
           WHERE f.filing_date >= ?
           GROUP BY f.doc_id
           ORDER BY f.filing_date DESC""",
        (start.isoformat(),),
    ).fetchall()

    with_sections = sum(1 for r in recent if r[4] > 0)
    total_recent = len(recent)
    type120 = [r for r in recent if r[2] == "120"]
    type120_with = sum(1 for r in type120 if r[4] > 0)

    sys.stdout.write(f"\n=== Quality Check ({DAYS} days) ===\n")
    sys.stdout.write(f"  Total filings:    {total_recent}\n")
    if total_recent:
        sys.stdout.write(f"  With sections:    {with_sections} ({with_sections/total_recent*100:.1f}%)\n")
    sys.stdout.write(f"  有報(120) total:  {len(type120)}\n")
    if type120:
        sys.stdout.write(f"  有報(120) w/sec:  {type120_with} ({type120_with/len(type120)*100:.1f}%)\n")

    sys.stdout.write(f"\n=== Sample (有報 with most sections) ===\n")
    type120_sorted = sorted(type120, key=lambda r: r[4], reverse=True)
    for r in type120_sorted[:10]:
        doc_id, company, dtype, fdate, sec_count, ft_chars = r
        secs = conn.execute(
            "SELECT section_key, char_count FROM filing_sections WHERE doc_id = ? AND section_key != 'full_text'",
            (doc_id,),
        ).fetchall()
        sec_info = ", ".join(f"{s[0]}={s[1]:,}" for s in secs) if secs else "(none)"
        sys.stdout.write(f"  {fdate} {company[:25]:25s} ft={ft_chars:>7,} | {sec_info}\n")

    sys.stdout.flush()
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
