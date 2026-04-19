#!/usr/bin/env python3
"""Test pipeline on specific listed company filings."""
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


TEST_DOCS = [
    "S100XYDT",  # ダイドーグループHD (sec=25900)
    "S100XXPI",  # コーセーアールイー (sec=32460)
]


async def main():
    index = SearchIndex()
    index.initialize()

    async with EdinetClient() as client:
        for doc_id in TEST_DOCS:
            sys.stdout.write(f"\n=== {doc_id} ===\n")
            sys.stdout.flush()
            t0 = time.monotonic()

            files = await client.download_and_extract_xbrl(doc_id)

            # Show file structure
            publics = sorted(
                (n, len(c)) for n, c in files.items()
                if "publicdoc/" in n.lower()
                and (n.lower().endswith(".htm") or n.lower().endswith(".html"))
            )
            for n, sz in publics:
                tag = " HEADER" if "_header_" in n.lower() else ""
                sys.stdout.write(f"  {sz:>9,}b {n.rsplit('/',1)[-1][:50]}{tag}\n")
            sys.stdout.flush()

            result = find_main_html_file(files)
            if result is None:
                sys.stdout.write("  NO BODY HTML\n")
                continue

            fname, content = result
            sys.stdout.write(f"  -> Selected: {fname[:50]} ({len(content):,}b)\n")
            sys.stdout.flush()

            sections = extract_sections(content)
            metadata = extract_filing_metadata(
                {"docID": doc_id, "filerName": "test", "docTypeCode": "120",
                 "submitDateTime": "2026-04-14", "edinetCode": "E00000"}
            )
            sec_keys = sorted(k for k in sections if k != "full_text")
            ft_len = len(sections.get("full_text", ""))
            elapsed = time.monotonic() - t0

            sys.stdout.write(f"  ft={ft_len:,} chars, {len(sec_keys)} sections [{elapsed:.1f}s]\n")
            for k in sec_keys:
                preview = sections[k][:100].replace("\n", " ").strip()
                sys.stdout.write(f"  {k:25s}: {len(sections[k]):>6,} chars | {preview}...\n")
            sys.stdout.flush()

            # Verify DB write
            index.upsert_filing(metadata, sections)
            sys.stdout.write("  DB upsert OK\n")
            sys.stdout.flush()

    sys.stdout.write("\nAll done.\n")


if __name__ == "__main__":
    asyncio.run(main())
