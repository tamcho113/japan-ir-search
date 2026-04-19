"""Index builder — downloads filings from EDINET and builds the search index."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, timedelta
from typing import Any

from .config import INDEXABLE_DOC_TYPES_ALL, INDEXABLE_DOC_TYPES_HIGH
from .edinet_client import EdinetClient
from .extractor import extract_filing_metadata, extract_sections, find_main_html_file
from .index import SearchIndex

logger = logging.getLogger(__name__)


async def build_index_for_date(
    client: EdinetClient,
    index: SearchIndex,
    target_date: str,
    *,
    target_types: set[str] | None = None,
    force: bool = False,
) -> int:
    """Download and index all filings for a given date.

    Returns:
        Number of filings indexed.
    """
    if target_types is None:
        target_types = INDEXABLE_DOC_TYPES_ALL
    logger.info("Fetching document list for %s", target_date)
    docs = await client.get_documents_by_date(target_date, doc_type="2")

    indexed = 0
    skipped = 0
    no_html = 0
    errors = 0
    docs_target = 0

    for doc in docs:
        doc_id = doc.get("docID")
        doc_type_code = doc.get("docTypeCode", "")
        if not doc_id:
            continue

        # Only index target document types
        if doc_type_code not in target_types:
            continue
        docs_target += 1

        # Skip if already indexed (unless force)
        if not force and index.has_filing(doc_id):
            logger.debug("Skipping %s (already indexed)", doc_id)
            skipped += 1
            continue

        logger.info("Indexing %s: %s (%s)", doc_id, doc.get("filerName", "?"), doc.get("docDescription", ""))

        try:
            files = await client.download_and_extract_xbrl(doc_id)
            main_file = find_main_html_file(files)
            if main_file is None:
                logger.warning("No HTML file found for %s", doc_id)
                no_html += 1
                continue

            _, html_bytes = main_file
            sections = extract_sections(html_bytes)
            metadata = extract_filing_metadata(doc)
            index.upsert_filing(metadata, sections)
            indexed += 1

        except Exception:
            errors += 1
            logger.exception("Failed to index %s", doc_id)

    return {
        "indexed": indexed,
        "skipped": skipped,
        "no_html": no_html,
        "errors": errors,
        "docs_found": len(docs),
        "docs_target": docs_target,
    }


async def build_index(
    api_key: str | None = None,
    db_path: str | None = None,
    *,
    years: int = 1,
    doc_types: str = "all",
    force: bool = False,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Build the full search index for the specified number of years.

    Args:
        api_key: EDINET API key.
        db_path: Path to SQLite database.
        years: Number of years to index (from today backwards).
        doc_types: "all" for HIGH+MEDIUM, "high" for HIGH only.
        force: Re-index even if already indexed.
        progress_callback: Optional callable(date_str, indexed_count, total_for_date) for progress.

    Returns:
        Summary statistics.
    """
    target_types = INDEXABLE_DOC_TYPES_ALL if doc_types == "all" else INDEXABLE_DOC_TYPES_HIGH
    index = SearchIndex(db_path)
    index.initialize()

    total_indexed = 0
    total_skipped = 0
    total_errors = 0
    end_date = date.today()
    start_date = end_date - timedelta(days=365 * years)
    current = start_date
    total_days = (end_date - start_date).days

    async with EdinetClient(api_key) as client:
        while current <= end_date:
            date_str = current.isoformat()
            day_num = (current - start_date).days
            # Skip weekends (EDINET has no filings on weekends)
            if current.weekday() < 5:
                try:
                    client.reset_metrics()
                    t0 = time.monotonic()
                    result = await build_index_for_date(
                        client, index, date_str,
                        target_types=target_types, force=force,
                    )
                    elapsed = time.monotonic() - t0
                    count = result["indexed"]
                    total_indexed += count
                    total_skipped += result["skipped"]

                    # Record build metrics
                    api_metrics = client.get_metrics()
                    index.record_build_metrics({
                        "build_date": date.today().isoformat(),
                        "target_date": date_str,
                        "docs_found": result["docs_found"],
                        "docs_target": result["docs_target"],
                        "docs_indexed": result["indexed"],
                        "docs_skipped": result["skipped"],
                        "docs_no_html": result["no_html"],
                        "docs_error": result["errors"],
                        "elapsed_seconds": round(elapsed, 1),
                        **api_metrics,
                    })

                    if progress_callback:
                        progress_callback(date_str, count, day_num, total_days)
                except Exception:
                    total_errors += 1
                    logger.exception("Error processing date %s", date_str)
            current += timedelta(days=1)

    stats = index.stats()
    stats["newly_indexed"] = total_indexed
    stats["errors"] = total_errors
    return stats
