"""CLI for japan-ir-search: build index, search, and serve MCP."""

from __future__ import annotations

import asyncio
import logging
import sys

import click


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
def main(verbose: bool) -> None:
    """japan-ir-search: Full-text search for Japanese securities filings."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


@main.command()
@click.option("--years", "-y", default=1, help="Number of years to index (default: 1)")
@click.option("--doc-types", "-t", type=click.Choice(["all", "high"]), default="all",
              help="Document types: 'all' (HIGH+MEDIUM) or 'high' only (default: all)")
@click.option("--force", "-f", is_flag=True, help="Re-index existing filings")
@click.option("--db-path", type=str, default=None, help="Custom database path")
@click.option("--api-key", type=str, default=None, help="EDINET API key (or set EDINET_API_KEY)")
def build(years: int, doc_types: str, force: bool, db_path: str | None, api_key: str | None) -> None:
    """Build the search index by downloading filings from EDINET."""
    from .builder import build_index

    def progress(date_str: str, count: int, day_num: int, total_days: int) -> None:
        pct = day_num / total_days * 100 if total_days > 0 else 0
        if count > 0:
            click.echo(f"  [{pct:5.1f}%] {date_str}: indexed {count} filing(s)")
        elif day_num % 30 == 0:  # Progress heartbeat every ~30 days
            click.echo(f"  [{pct:5.1f}%] {date_str}: scanning...")

    type_label = "全種別 (HIGH+MEDIUM)" if doc_types == "all" else "主要種別のみ (HIGH)"
    click.echo(f"Building index: past {years} year(s), {type_label}")
    click.echo("EDINET APIレート制限を守るため、時間がかかります。")
    click.echo("中断しても再開時にスキップするので安全です。")
    click.echo()

    result = asyncio.run(
        build_index(
            api_key=api_key,
            db_path=db_path,
            years=years,
            doc_types=doc_types,
            force=force,
            progress_callback=progress,
        )
    )

    click.echo()
    click.echo(f"Done! Indexed {result['newly_indexed']} new filing(s).")
    if result.get("errors", 0) > 0:
        click.echo(f"Errors: {result['errors']} (will be retried on next run)")
    click.echo(f"Total: {result['filings_indexed']} filings from {result['companies_indexed']} companies")
    click.echo(f"Database: {result['db_path']}")


@main.command()
@click.option("--date", "-d", type=str, default=None,
              help="Date to update (YYYY-MM-DD). Defaults to today.")
@click.option("--doc-types", "-t", type=click.Choice(["all", "high"]), default="all",
              help="Document types: 'all' (HIGH+MEDIUM) or 'high' only (default: all)")
@click.option("--db-path", type=str, default=None, help="Custom database path")
@click.option("--api-key", type=str, default=None, help="EDINET API key (or set EDINET_API_KEY)")
def update(date: str | None, doc_types: str, db_path: str | None, api_key: str | None) -> None:
    """Fetch and index today's (or a specific date's) new filings."""
    from datetime import date as date_cls

    from .builder import build_index_for_date
    from .config import INDEXABLE_DOC_TYPES_ALL, INDEXABLE_DOC_TYPES_HIGH
    from .edinet_client import EdinetClient
    from .index import SearchIndex

    target = date or date_cls.today().isoformat()
    target_types = INDEXABLE_DOC_TYPES_ALL if doc_types == "all" else INDEXABLE_DOC_TYPES_HIGH

    click.echo(f"Updating index for {target} ...")

    async def _run() -> dict:
        index = SearchIndex(db_path)
        index.initialize()
        async with EdinetClient(api_key) as client:
            return await build_index_for_date(client, index, target, target_types=target_types)

    result = asyncio.run(_run())
    click.echo(f"Done! Indexed {result['indexed']} new filing(s) for {target}.")
    if result["skipped"]:
        click.echo(f"  Skipped: {result['skipped']} (already indexed)")
    if result["no_html"]:
        click.echo(f"  No HTML: {result['no_html']}")
    if result["errors"]:
        click.echo(f"  Errors: {result['errors']}")


@main.command()
@click.option("--json-output", "-j", is_flag=True, help="Output as JSON")
@click.option("--db-path", type=str, default=None, help="Custom database path")
def analyze(json_output: bool, db_path: str | None) -> None:
    """Analyze indexed data quality and build metrics."""
    import json as json_mod

    from .index import SearchIndex

    index = SearchIndex(db_path)
    # Don't call initialize() — it tries to write, which conflicts with ongoing builds.
    # analyze() uses readonly connections.
    data = index.analyze()

    if json_output:
        click.echo(json_mod.dumps(data, ensure_ascii=False, indent=2))
        return

    s = data["summary"]
    q = data["quality"]

    click.echo("=" * 60)
    click.echo("  DATA ANALYSIS REPORT")
    click.echo("=" * 60)

    click.echo(f"\n📊 Summary")
    click.echo(f"  Filings:     {s['total_filings']:,}")
    click.echo(f"  Companies:   {s['total_companies']:,}")
    click.echo(f"  Sections:    {s['total_sections']:,}")
    click.echo(f"  Characters:  {s['total_characters']:,}")
    click.echo(f"  DB Size:     {s['db_size_mb']} MB")
    click.echo(f"  Date Range:  {s['date_range_start']} ~ {s['date_range_end']}")

    click.echo(f"\n📋 Quality")
    click.echo(f"  Section extraction rate: {q['section_extraction_rate_pct']}%")
    click.echo(f"  Tiny sections (<100 chars): {q['tiny_sections_count']}")

    click.echo(f"\n📁 Document Type Distribution")
    for d in data["doc_type_distribution"]:
        click.echo(f"  {d['doc_type_code']:>4s}: {d['count']:>5,} filings")

    click.echo(f"\n📝 Section Stats")
    for sec in data["section_stats"]:
        click.echo(
            f"  {sec['section_key']:25s}: {sec['count']:>5,} entries, "
            f"avg {sec['avg_chars']:>6,} chars "
            f"(min {sec['min_chars']:,} / max {sec['max_chars']:,})"
        )

    click.echo(f"\n📅 Monthly Volume (recent 12)")
    for m in data["monthly_volume"][-12:]:
        bar = "█" * min(50, m["filings"] // 10)
        click.echo(f"  {m['month']}: {m['filings']:>4} filings, {m['companies']:>4} companies {bar}")

    click.echo(f"\n📈 Text Volume by Doc Type")
    for d in data["doc_type_text_size"]:
        total_mb = d["total_chars"] / 1024 / 1024
        click.echo(
            f"  {d['doc_type_code']:>4s}: {d['filings']:>5,} filings, "
            f"avg {d['avg_section_chars']:>6,} chars/doc, "
            f"total {total_mb:.1f} MB"
        )

    click.echo(f"\n🏢 Top 20 Companies by Text Volume")
    for i, c in enumerate(data["top_companies_by_volume"], 1):
        mb = c["total_chars"] / 1024 / 1024
        click.echo(
            f"  {i:>2}. {c['company_name'][:30]:30s} "
            f"{c['filings']:>3} filings, {mb:.1f} MB"
        )

    # Build metrics if available
    build_summary = index.get_build_metrics_summary()
    if "message" not in build_summary:
        click.echo(f"\n🔧 Build Metrics")
        click.echo(f"  Dates processed:   {build_summary['total_dates']}")
        click.echo(f"  Docs found:        {build_summary['total_docs_found']:,}")
        click.echo(f"  Docs indexed:      {build_summary['total_docs_indexed']:,}")
        click.echo(f"  No HTML:           {build_summary['total_no_html']:,}")
        click.echo(f"  Errors:            {build_summary['total_errors']:,}")
        click.echo(f"  API calls:         {build_summary['total_api_calls']:,}")
        click.echo(f"  Retries:           {build_summary['total_retries']:,}")
        click.echo(f"  429 responses:     {build_summary['total_429s']:,}")
        click.echo(f"  5xx responses:     {build_summary['total_5xx']:,}")
        if build_summary["overall_avg_ms"]:
            click.echo(f"  Avg response:      {build_summary['overall_avg_ms']} ms")
        if build_summary["overall_max_ms"]:
            click.echo(f"  Max response:      {build_summary['overall_max_ms']:.0f} ms")
        if build_summary["total_bytes"]:
            gb = build_summary["total_bytes"] / 1024 / 1024 / 1024
            click.echo(f"  Downloaded:        {gb:.2f} GB")
        if build_summary["total_elapsed_s"]:
            hours = build_summary["total_elapsed_s"] / 3600
            click.echo(f"  Total time:        {hours:.1f} hours")

    click.echo()


@main.command()
@click.argument("query")
@click.option("--section", "-s", type=str, default=None, help="Section filter (e.g., risk_factors)")
@click.option("--company", "-c", type=str, default=None, help="Company name filter")
@click.option("--limit", "-n", default=10, help="Max results")
@click.option("--db-path", type=str, default=None, help="Custom database path")
def search(query: str, section: str | None, company: str | None, limit: int, db_path: str | None) -> None:
    """Search indexed filings."""
    from .index import SearchIndex

    index = SearchIndex(db_path)
    index.initialize()
    results = index.search(query, section=section, company=company, limit=limit)

    if not results:
        click.echo("No results found.")
        return

    for i, r in enumerate(results, 1):
        click.echo(f"\n{'='*60}")
        click.echo(f"[{i}] {r['company_name']} ({r['edinet_code']})")
        click.echo(f"    Filing: {r['doc_id']} ({r['filing_date']})")
        click.echo(f"    Section: {r['section_key']}")
        click.echo(f"    {r['snippet']}")


@main.command()
@click.option("--db-path", type=str, default=None, help="Custom database path")
def stats(db_path: str | None) -> None:
    """Show index statistics."""
    from .index import SearchIndex

    index = SearchIndex(db_path)
    index.initialize()
    s = index.stats()
    click.echo(f"Filings indexed:   {s['filings_indexed']}")
    click.echo(f"Companies indexed:  {s['companies_indexed']}")
    click.echo(f"Sections indexed:   {s['sections_indexed']}")
    click.echo(f"Total characters:   {s['total_characters']:,}")
    click.echo(f"Database path:      {s['db_path']}")


@main.command("rebuild-sections")
@click.option("--db-path", type=str, default=None, help="Custom database path")
@click.option("--batch-size", default=500, help="Batch size for processing")
def rebuild_sections(db_path: str | None, batch_size: int) -> None:
    """Re-extract sections from stored full_text using improved patterns.

    Processes existing full_text data without re-downloading from EDINET.
    For HTML entries, re-parses to plain text first.
    Then applies 【】bracket-based section splitting.

    Note: Filings where the wrong HTML file was originally selected (header
    instead of body) will not be fixed by this command. Use `build --force`
    to re-download and fully re-process those filings.
    """
    import re as re_mod

    from lxml import html as lxml_html

    from .extractor import split_into_sections
    from .index import SearchIndex

    index = SearchIndex(db_path)

    # Count total
    with index._connect(readonly=True) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM filing_sections WHERE section_key = 'full_text'"
        ).fetchone()[0]

    click.echo(f"Processing {total:,} filings with improved section extraction...")
    click.echo(f"  - Re-parsing raw HTML entries to plain text")
    click.echo(f"  - Applying 【】bracket section patterns")
    click.echo()

    processed = 0
    improved = 0
    html_fixed = 0
    offset = 0

    while True:
        with index._connect(readonly=True) as conn:
            rows = conn.execute(
                """SELECT fs.doc_id, f.company_name, fs.section_text
                   FROM filing_sections fs
                   JOIN filings f ON f.doc_id = fs.doc_id
                   WHERE fs.section_key = 'full_text'
                   ORDER BY fs.doc_id
                   LIMIT ? OFFSET ?""",
                (batch_size, offset),
            ).fetchall()

        if not rows:
            break
        offset += batch_size

        for row in rows:
            doc_id, company_name, full_text = row[0], row[1], row[2]
            processed += 1

            text = full_text
            was_html = False

            # If stored as raw HTML, re-parse to text
            if text.lstrip().startswith("<"):
                was_html = True
                try:
                    doc = lxml_html.fromstring(text.encode("utf-8"))
                    for el in doc.iter("script", "style"):
                        if el.getparent() is not None:
                            el.getparent().remove(el)
                    text = doc.text_content()
                except Exception:
                    pass  # keep original text

            # Try section splitting
            sections = split_into_sections(text)

            if sections or was_html:
                # Clean full_text
                clean_text = re_mod.sub(r"\s+", " ", text).strip()
                if len(clean_text) > 1_000_000:
                    clean_text = clean_text[:1_000_000] + " [...truncated...]"
                sections["full_text"] = clean_text
                index.update_sections(doc_id, company_name, sections)

                if sections.keys() - {"full_text"}:
                    improved += 1
                if was_html:
                    html_fixed += 1

        if processed % 2000 == 0 or processed == total:
            click.echo(
                f"  [{processed:>6,}/{total:,}] "
                f"sections extracted: {improved:,}, HTML fixed: {html_fixed:,}"
            )

    click.echo()
    click.echo(f"Done!")
    click.echo(f"  Processed:          {processed:,}")
    click.echo(f"  Sections extracted:  {improved:,}")
    click.echo(f"  HTML re-parsed:      {html_fixed:,}")
    click.echo(f"  Unchanged:           {processed - max(improved, html_fixed):,}")
    if processed - improved > 0:
        click.echo()
        click.echo(
            f"  Note: {processed - improved:,} filings still have no sections. "
            f"These likely had the wrong HTML file selected (cover page instead "
            f"of body). Run `japan-ir-search build --force` to re-download and "
            f"fully re-process them."
        )


@main.command()
@click.option("--since", "-s", default=7, help="Lookback window in days (default: 7)")
@click.option("--json-output", "-j", is_flag=True, help="Output as JSON")
@click.option("--db-path", type=str, default=None, help="Custom database path")
def usage(since: int, json_output: bool, db_path: str | None) -> None:
    """Show MCP tool usage analytics over the last N days."""
    import json as json_mod

    from .index import SearchIndex

    index = SearchIndex(db_path)
    data = index.get_usage_summary(since_days=since)

    if json_output:
        click.echo(json_mod.dumps(data, ensure_ascii=False, indent=2))
        return

    if "message" in data:
        click.echo(data["message"])
        return

    click.echo("=" * 60)
    click.echo(f"  MCP USAGE — last {data['since_days']} days")
    click.echo("=" * 60)
    click.echo(f"\nTotal calls: {data['total_calls']:,}   Errors: {data['errors']}")

    click.echo(f"\n📞 By Tool")
    for t in data["by_tool"]:
        avg_ms = t["avg_ms"] if t["avg_ms"] is not None else 0
        avg_results = t["avg_results"] if t["avg_results"] is not None else 0
        click.echo(
            f"  {t['tool_name']:25s}: {t['calls']:>5,} calls  "
            f"avg {avg_ms:>5,} ms  avg {avg_results:>4} results  "
            f"errors {t['errors']}"
        )

    if data.get("section_usage"):
        click.echo(f"\n📂 Section Filters Used")
        for s in data["section_usage"]:
            click.echo(f"  {s['section_key']:25s}: {s['calls']:>4,}")

    if data.get("top_queries"):
        click.echo(f"\n🔍 Top Queries (preview, max 30 chars)")
        for q in data["top_queries"]:
            preview = q["query_preview"] or "(empty)"
            click.echo(f"  {q['calls']:>3,}× {preview}")

    if data.get("daily"):
        click.echo(f"\n📅 Daily")
        for d in data["daily"]:
            bar = "█" * min(40, d["calls"])
            click.echo(f"  {d['day']}: {d['calls']:>4,} {bar}")
    click.echo()


@main.command()
@click.option("--transport", "-t", type=click.Choice(["stdio", "sse", "streamable-http"]),
              default="stdio", help="Transport protocol (default: stdio)")
def serve(transport: str) -> None:
    """Start the MCP server."""
    from .server import run_server

    run_server(transport=transport)


if __name__ == "__main__":
    main()
