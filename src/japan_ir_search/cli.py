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


@main.command()
def serve() -> None:
    """Start the MCP server."""
    from .server import run_server

    run_server()


if __name__ == "__main__":
    main()
