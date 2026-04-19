#!/usr/bin/env python3
"""Quick test: download 3 type=120 filings and test extraction pipeline."""
import asyncio
import sys
import time

from japan_ir_search.edinet_client import EdinetClient
from japan_ir_search.extractor import (
    extract_filing_metadata,
    extract_sections,
    find_main_html_file,
)
from japan_ir_search.index import SearchIndex


async def main():
    index = SearchIndex()
    index.initialize()

    async with EdinetClient() as client:
        docs = await client.get_documents_by_date("2026-04-14", doc_type="2")
        type120 = [d for d in docs if d.get("docTypeCode") == "120"]
        sys.stdout.write(f"有報(120) on 2026-04-14: {len(type120)} docs\n\n")
        sys.stdout.flush()

        for d in type120[:5]:
            doc_id = d["docID"]
            name = d.get("filerName", "?")
            sys.stdout.write(f"  {doc_id} {name}... ")
            sys.stdout.flush()
            t0 = time.monotonic()

            files = await client.download_and_extract_xbrl(doc_id)
            result = find_main_html_file(files)
            if result is None:
                sys.stdout.write("NO BODY HTML\n")
                sys.stdout.flush()
                continue

            fname, content = result
            sections = extract_sections(content)
            metadata = extract_filing_metadata(d)

            sec_keys = sorted(k for k in sections if k != "full_text")
            ft_len = len(sections.get("full_text", ""))
            elapsed = time.monotonic() - t0

            # Write to DB
            index.upsert_filing(metadata, sections)

            sys.stdout.write(
                f"OK [{elapsed:.1f}s] ft={ft_len:,} sections={sec_keys}\n"
            )
            sys.stdout.flush()

            for k in sec_keys:
                preview = sections[k][:80].replace("\n", " ").strip()
                sys.stdout.write(f"    {k:25s}: {len(sections[k]):>6,} chars | {preview}...\n")
                sys.stdout.flush()

    sys.stdout.write("\nDone.\n")
    sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
