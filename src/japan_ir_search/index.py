"""SQLite FTS5 full-text search index for filing documents."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from .config import DB_PATH

_SCHEMA_VERSION = 2

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS filings (
    doc_id TEXT PRIMARY KEY,
    edinet_code TEXT NOT NULL,
    sec_code TEXT,
    company_name TEXT NOT NULL,
    doc_description TEXT,
    doc_type_code TEXT,
    filing_date TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_filings_edinet_code ON filings(edinet_code);
CREATE INDEX IF NOT EXISTS idx_filings_sec_code ON filings(sec_code);
CREATE INDEX IF NOT EXISTS idx_filings_company_name ON filings(company_name);
CREATE INDEX IF NOT EXISTS idx_filings_filing_date ON filings(filing_date);

CREATE TABLE IF NOT EXISTS filing_sections (
    doc_id TEXT NOT NULL,
    section_key TEXT NOT NULL,
    section_text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    PRIMARY KEY (doc_id, section_key),
    FOREIGN KEY (doc_id) REFERENCES filings(doc_id)
);

-- Build metrics for monitoring
CREATE TABLE IF NOT EXISTS build_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_date TEXT NOT NULL,
    target_date TEXT NOT NULL,
    docs_found INTEGER DEFAULT 0,
    docs_target INTEGER DEFAULT 0,
    docs_indexed INTEGER DEFAULT 0,
    docs_skipped INTEGER DEFAULT 0,
    docs_no_html INTEGER DEFAULT 0,
    docs_error INTEGER DEFAULT 0,
    api_calls INTEGER DEFAULT 0,
    api_retries INTEGER DEFAULT 0,
    api_429_count INTEGER DEFAULT 0,
    api_5xx_count INTEGER DEFAULT 0,
    avg_response_ms REAL,
    max_response_ms REAL,
    total_bytes_downloaded INTEGER DEFAULT 0,
    elapsed_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# FTS5 creation handled separately (slow IF NOT EXISTS check on large DBs)
_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS filings_fts USING fts5(
    doc_id UNINDEXED,
    company_name,
    section_key UNINDEXED,
    section_text,
    content='',
    tokenize='trigram'
);
"""


class SearchIndex:
    """SQLite FTS5-based full-text search index."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = str(db_path or DB_PATH)

    def _ensure_dir(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self, readonly: bool = False) -> Generator[sqlite3.Connection, None, None]:
        self._ensure_dir()
        if readonly:
            uri = f"file:{self._db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=60)
        else:
            conn = sqlite3.connect(self._db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create tables and FTS index if they don't exist."""
        with self._connect() as conn:
            # Fast path: if schema_version table exists and is current, skip DDL
            try:
                cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
                row = cur.fetchone()
                if row is not None and row["version"] >= _SCHEMA_VERSION:
                    return
            except Exception:
                row = None
            # Full initialization for new or outdated databases
            conn.executescript(_CREATE_TABLES)
            conn.executescript(_CREATE_FTS)
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
            elif row["version"] < _SCHEMA_VERSION:
                self._migrate(conn, row["version"])
            conn.commit()

    def _migrate(self, conn: sqlite3.Connection, from_version: int) -> None:
        """Run schema migrations sequentially."""
        if from_version < 2:
            # v1→v2: Convert FTS5 to contentless mode (content='')
            # Check if the existing FTS table works (read-only compatible)
            try:
                conn.execute("SELECT 1 FROM filings_fts LIMIT 1")
                # v1 FTS table exists and works — skip expensive rebuild
                # (v1 content-bearing FTS is fully read-compatible with v2 code)
                conn.execute(
                    "UPDATE schema_version SET version = ?", (_SCHEMA_VERSION,)
                )
                return
            except Exception:
                pass
            # Full migration needed: drop and recreate as contentless
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
            # Repopulate from filing_sections using INSERT...SELECT (memory-efficient)
            conn.execute(
                "INSERT INTO filings_fts (rowid, doc_id, company_name, section_key, section_text) "
                "SELECT fs.rowid, fs.doc_id, f.company_name, fs.section_key, fs.section_text "
                "FROM filing_sections fs JOIN filings f ON f.doc_id = fs.doc_id"
            )
            conn.execute(
                "UPDATE schema_version SET version = ?", (_SCHEMA_VERSION,)
            )

    def upsert_filing(
        self,
        metadata: dict[str, Any],
        sections: dict[str, str],
    ) -> None:
        """Insert or update a filing and its searchable sections."""
        doc_id = metadata["doc_id"]
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO filings
                   (doc_id, edinet_code, sec_code, company_name,
                    doc_description, doc_type_code, filing_date,
                    period_start, period_end)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id,
                    metadata.get("edinet_code", ""),
                    metadata.get("sec_code"),
                    metadata.get("company_name", ""),
                    metadata.get("doc_description"),
                    metadata.get("doc_type_code"),
                    metadata.get("filing_date", ""),
                    metadata.get("period_start"),
                    metadata.get("period_end"),
                ),
            )
            # Contentless FTS: delete old entries before removing from filing_sections
            company_name = metadata.get("company_name", "")
            old_rows = conn.execute(
                "SELECT rowid, section_key, section_text FROM filing_sections WHERE doc_id = ?",
                (doc_id,),
            ).fetchall()
            for old_row in old_rows:
                conn.execute(
                    "INSERT INTO filings_fts(filings_fts, rowid, doc_id, company_name, section_key, section_text) "
                    "VALUES('delete', ?, ?, ?, ?, ?)",
                    (old_row[0], doc_id, company_name, old_row[1], old_row[2]),
                )
            conn.execute("DELETE FROM filing_sections WHERE doc_id = ?", (doc_id,))
            for section_key, text in sections.items():
                if not text.strip():
                    continue
                conn.execute(
                    """INSERT INTO filing_sections (doc_id, section_key, section_text, char_count)
                       VALUES (?, ?, ?, ?)""",
                    (doc_id, section_key, text, len(text)),
                )
                # Contentless FTS: supply rowid explicitly to enable future deletes
                rowid = conn.execute("SELECT rowid FROM filing_sections WHERE doc_id = ? AND section_key = ?", (doc_id, section_key)).fetchone()[0]
                conn.execute(
                    """INSERT INTO filings_fts (rowid, doc_id, company_name, section_key, section_text)
                       VALUES (?, ?, ?, ?, ?)""",
                    (rowid, doc_id, company_name, section_key, text),
                )
            conn.commit()

    def update_sections(
        self,
        doc_id: str,
        company_name: str,
        sections: dict[str, str],
    ) -> None:
        """Replace all sections for an existing filing (used by rebuild-sections)."""
        with self._connect() as conn:
            # Contentless FTS: delete old entries by rowid + original content
            old_rows = conn.execute(
                "SELECT rowid, section_key, section_text FROM filing_sections WHERE doc_id = ?",
                (doc_id,),
            ).fetchall()
            for old_row in old_rows:
                conn.execute(
                    "INSERT INTO filings_fts(filings_fts, rowid, doc_id, company_name, section_key, section_text) "
                    "VALUES('delete', ?, ?, ?, ?, ?)",
                    (old_row[0], doc_id, company_name, old_row[1], old_row[2]),
                )
            conn.execute("DELETE FROM filing_sections WHERE doc_id = ?", (doc_id,))

            for section_key, text in sections.items():
                if not text.strip():
                    continue
                conn.execute(
                    """INSERT INTO filing_sections (doc_id, section_key, section_text, char_count)
                       VALUES (?, ?, ?, ?)""",
                    (doc_id, section_key, text, len(text)),
                )
                rowid = conn.execute("SELECT rowid FROM filing_sections WHERE doc_id = ? AND section_key = ?", (doc_id, section_key)).fetchone()[0]
                conn.execute(
                    """INSERT INTO filings_fts (rowid, doc_id, company_name, section_key, section_text)
                       VALUES (?, ?, ?, ?, ?)""",
                    (rowid, doc_id, company_name, section_key, text),
                )
            conn.commit()

    def search(
        self,
        query: str,
        *,
        section: str | None = None,
        company: str | None = None,
        limit: int = 20,
        snippet_size: int = 200,
    ) -> list[dict[str, Any]]:
        """Full-text search across indexed filings.

        Uses FTS5 MATCH for queries of 3+ characters (fast trigram index).
        Falls back to LIKE for shorter queries (full scan, slower but correct).

        Args:
            query: Search query (FTS5 syntax supported for 3+ char queries).
            section: Limit to a specific section key (e.g., "risk_factors").
            company: Filter by company name (partial match).
            limit: Max results.
            snippet_size: Approximate snippet character length.

        Returns:
            List of dicts with filing metadata, section key, and text snippet.
        """
        # Determine if query is long enough for trigram FTS5
        # Strip quotes/operators for length check
        stripped = query.strip().strip('"').strip("'")
        use_fts = len(stripped) >= 3

        with self._connect() as conn:
            if use_fts:
                return self._search_fts(conn, query, section=section, company=company, limit=limit)
            else:
                return self._search_like(conn, query, section=section, company=company, limit=limit)

    def _search_fts(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        section: str | None = None,
        company: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """FTS5 MATCH search (fast, for 3+ character queries)."""
        where_clauses: list[str] = []
        params: list[Any] = [query]

        if section:
            where_clauses.append("fs.section_key = ?")
            params.append(section)

        if company:
            where_clauses.append("f.company_name LIKE ?")
            params.append(f"%{company}%")

        where_sql = ""
        if where_clauses:
            where_sql = "AND " + " AND ".join(where_clauses)

        params.append(limit)
        # Join FTS results to filing_sections/filings via doc_id + section_key
        # (rowid matching is unreliable between FTS and regular tables)
        sql = f"""
            SELECT
                f.doc_id,
                f.edinet_code,
                f.sec_code,
                f.company_name,
                f.doc_description,
                f.filing_date,
                f.period_start,
                f.period_end,
                fs.section_key,
                fs.section_text,
                fts.rank
            FROM filings_fts fts
            JOIN filing_sections fs
                ON fs.doc_id = fts.doc_id AND fs.section_key = fts.section_key
            JOIN filings f ON f.doc_id = fts.doc_id
            WHERE filings_fts MATCH ?
            {where_sql}
            ORDER BY fts.rank
            LIMIT ?
        """
        rows = conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            section_text = d.pop("section_text", "")
            # Generate snippet with highlighted match
            stripped = query.strip().strip('"').strip("'")
            pos = section_text.find(stripped)
            if pos >= 0:
                start = max(0, pos - 80)
                end = min(len(section_text), pos + len(stripped) + 80)
                before = "..." if start > 0 else ""
                after = "..." if end < len(section_text) else ""
                snip = section_text[start:end]
                snip = snip.replace(stripped, f"【{stripped}】", 1)
                d["snippet"] = f"{before}{snip}{after}"
            else:
                d["snippet"] = section_text[:160] + ("..." if len(section_text) > 160 else "")
            results.append(d)
        return results

    def _search_like(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        section: str | None = None,
        company: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """LIKE fallback search (slower, for 1-2 character queries)."""
        where_clauses = ["fs.section_text LIKE ?"]
        params: list[Any] = [f"%{query}%"]

        if section:
            where_clauses.append("fs.section_key = ?")
            params.append(section)

        if company:
            where_clauses.append("f.company_name LIKE ?")
            params.append(f"%{company}%")

        where_sql = " AND ".join(where_clauses)
        params.append(limit)

        sql = f"""
            SELECT
                f.doc_id,
                f.edinet_code,
                f.sec_code,
                f.company_name,
                f.doc_description,
                f.filing_date,
                f.period_start,
                f.period_end,
                fs.section_key,
                fs.section_text,
                0 AS rank
            FROM filing_sections fs
            JOIN filings f ON f.doc_id = fs.doc_id
            WHERE {where_sql}
            ORDER BY f.filing_date DESC
            LIMIT ?
        """
        rows = conn.execute(sql, params).fetchall()

        # Generate snippets manually for LIKE results
        results = []
        for row in rows:
            d = dict(row)
            text = d.pop("section_text", "")
            pos = text.find(query)
            if pos >= 0:
                start = max(0, pos - 80)
                end = min(len(text), pos + len(query) + 80)
                before = "..." if start > 0 else ""
                after = "..." if end < len(text) else ""
                snippet_text = text[start:end]
                # Highlight the match
                snippet_text = snippet_text.replace(query, f"【{query}】", 1)
                d["snippet"] = f"{before}{snippet_text}{after}"
            else:
                d["snippet"] = text[:160] + "..." if len(text) > 160 else text
            results.append(d)
        return results

    def get_section_text(self, doc_id: str, section_key: str) -> str | None:
        """Retrieve full section text for a specific filing and section."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT section_text FROM filing_sections WHERE doc_id = ? AND section_key = ?",
                (doc_id, section_key),
            ).fetchone()
            return row["section_text"] if row else None

    def get_filing_info(self, doc_id: str) -> dict[str, Any] | None:
        """Get filing metadata."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM filings WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_companies(self, query: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List indexed companies, optionally filtered by name."""
        with self._connect() as conn:
            if query:
                rows = conn.execute(
                    """SELECT DISTINCT edinet_code, sec_code, company_name,
                              COUNT(*) as filing_count
                       FROM filings
                       WHERE company_name LIKE ?
                       GROUP BY edinet_code
                       ORDER BY company_name
                       LIMIT ?""",
                    (f"%{query}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT DISTINCT edinet_code, sec_code, company_name,
                              COUNT(*) as filing_count
                       FROM filings
                       GROUP BY edinet_code
                       ORDER BY company_name
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        """Return index statistics."""
        with self._connect(readonly=True) as conn:
            filings_count = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
            sections_count = conn.execute("SELECT COUNT(*) FROM filing_sections").fetchone()[0]
            companies_count = conn.execute(
                "SELECT COUNT(DISTINCT edinet_code) FROM filings"
            ).fetchone()[0]
            # Note: SUM(char_count) over filing_sections is too slow on large DBs
            # (full table scan reads through section_text pages).
            # Use an indexed-only estimate from filings table instead.
            date_range = conn.execute(
                "SELECT MIN(filing_date), MAX(filing_date) FROM filings"
            ).fetchone()
            return {
                "filings_indexed": filings_count,
                "sections_indexed": sections_count,
                "companies_indexed": companies_count,
                "filing_date_range": {
                    "earliest": date_range[0],
                    "latest": date_range[1],
                } if date_range else None,
                "db_path": str(self._db_path),
            }

    def has_filing(self, doc_id: str) -> bool:
        """Check if a document is already indexed."""
        with self._connect(readonly=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM filings WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            return row is not None

    def analyze(self) -> dict[str, Any]:
        """Compute detailed analytics on the indexed data."""
        with self._connect(readonly=True) as conn:
            # --- Basic counts ---
            total_filings = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
            total_companies = conn.execute(
                "SELECT COUNT(DISTINCT edinet_code) FROM filings"
            ).fetchone()[0]
            total_sections = conn.execute("SELECT COUNT(*) FROM filing_sections").fetchone()[0]
            total_chars = conn.execute(
                "SELECT COALESCE(SUM(char_count), 0) FROM filing_sections"
            ).fetchone()[0]

            # --- Doc type distribution ---
            doc_type_dist = [
                dict(row) for row in conn.execute(
                    """SELECT doc_type_code, COUNT(*) as count
                       FROM filings GROUP BY doc_type_code ORDER BY count DESC"""
                ).fetchall()
            ]

            # --- Monthly filing volume ---
            monthly_volume = [
                dict(row) for row in conn.execute(
                    """SELECT substr(filing_date, 1, 7) as month,
                              COUNT(*) as filings,
                              COUNT(DISTINCT edinet_code) as companies
                       FROM filings GROUP BY month ORDER BY month"""
                ).fetchall()
            ]

            # --- Section extraction rates ---
            section_stats = [
                dict(row) for row in conn.execute(
                    """SELECT section_key,
                              COUNT(*) as count,
                              CAST(AVG(char_count) AS INTEGER) as avg_chars,
                              MIN(char_count) as min_chars,
                              MAX(char_count) as max_chars,
                              SUM(char_count) as total_chars
                       FROM filing_sections
                       GROUP BY section_key
                       ORDER BY count DESC"""
                ).fetchall()
            ]

            # --- Section extraction success rate (non-full_text sections per filing) ---
            filings_with_sections = conn.execute(
                """SELECT COUNT(DISTINCT doc_id) FROM filing_sections
                   WHERE section_key != 'full_text'"""
            ).fetchone()[0]
            section_success_rate = (
                filings_with_sections / total_filings * 100 if total_filings > 0 else 0
            )

            # --- Empty / tiny sections (< 100 chars, likely extraction failures) ---
            tiny_sections = conn.execute(
                """SELECT COUNT(*) FROM filing_sections
                   WHERE char_count < 100 AND section_key != 'full_text'"""
            ).fetchone()[0]

            # --- Doc type × avg text size ---
            doc_type_text_size = [
                dict(row) for row in conn.execute(
                    """SELECT f.doc_type_code,
                              COUNT(DISTINCT f.doc_id) as filings,
                              CAST(AVG(fs.char_count) AS INTEGER) as avg_section_chars,
                              SUM(fs.char_count) as total_chars
                       FROM filings f
                       JOIN filing_sections fs ON f.doc_id = fs.doc_id
                       WHERE fs.section_key = 'full_text'
                       GROUP BY f.doc_type_code
                       ORDER BY total_chars DESC"""
                ).fetchall()
            ]

            # --- Top 20 companies by text volume ---
            top_companies = [
                dict(row) for row in conn.execute(
                    """SELECT f.company_name, f.edinet_code,
                              COUNT(DISTINCT f.doc_id) as filings,
                              SUM(fs.char_count) as total_chars
                       FROM filings f
                       JOIN filing_sections fs ON f.doc_id = fs.doc_id
                       GROUP BY f.edinet_code
                       ORDER BY total_chars DESC
                       LIMIT 20"""
                ).fetchall()
            ]

            # --- DB file size ---
            db_size_bytes = Path(self._db_path).stat().st_size if Path(self._db_path).exists() else 0

            # --- Date range ---
            date_range = conn.execute(
                "SELECT MIN(filing_date), MAX(filing_date) FROM filings"
            ).fetchone()

            return {
                "summary": {
                    "total_filings": total_filings,
                    "total_companies": total_companies,
                    "total_sections": total_sections,
                    "total_characters": total_chars,
                    "db_size_mb": round(db_size_bytes / 1024 / 1024, 1),
                    "date_range_start": date_range[0] if date_range else None,
                    "date_range_end": date_range[1] if date_range else None,
                },
                "quality": {
                    "section_extraction_rate_pct": round(section_success_rate, 1),
                    "tiny_sections_count": tiny_sections,
                },
                "doc_type_distribution": doc_type_dist,
                "doc_type_text_size": doc_type_text_size,
                "monthly_volume": monthly_volume,
                "section_stats": section_stats,
                "top_companies_by_volume": top_companies,
            }

    def record_build_metrics(self, metrics: dict[str, Any]) -> None:
        """Record build metrics for a single date."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO build_metrics
                   (build_date, target_date, docs_found, docs_target,
                    docs_indexed, docs_skipped, docs_no_html, docs_error,
                    api_calls, api_retries, api_429_count, api_5xx_count,
                    avg_response_ms, max_response_ms, total_bytes_downloaded,
                    elapsed_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    metrics.get("build_date", ""),
                    metrics.get("target_date", ""),
                    metrics.get("docs_found", 0),
                    metrics.get("docs_target", 0),
                    metrics.get("docs_indexed", 0),
                    metrics.get("docs_skipped", 0),
                    metrics.get("docs_no_html", 0),
                    metrics.get("docs_error", 0),
                    metrics.get("api_calls", 0),
                    metrics.get("api_retries", 0),
                    metrics.get("api_429_count", 0),
                    metrics.get("api_5xx_count", 0),
                    metrics.get("avg_response_ms"),
                    metrics.get("max_response_ms"),
                    metrics.get("total_bytes_downloaded", 0),
                    metrics.get("elapsed_seconds"),
                ),
            )
            conn.commit()

    def get_build_metrics_summary(self) -> dict[str, Any]:
        """Summarize build metrics across all recorded runs."""
        with self._connect(readonly=True) as conn:
            # Table may not exist if build hasn't run with metrics yet
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='build_metrics'"
            ).fetchone()
            if tables is None:
                return {"message": "No build metrics recorded yet (table not created)"}
            row = conn.execute(
                """SELECT
                       COUNT(*) as total_dates,
                       SUM(docs_found) as total_docs_found,
                       SUM(docs_indexed) as total_docs_indexed,
                       SUM(docs_skipped) as total_docs_skipped,
                       SUM(docs_no_html) as total_no_html,
                       SUM(docs_error) as total_errors,
                       SUM(api_calls) as total_api_calls,
                       SUM(api_retries) as total_retries,
                       SUM(api_429_count) as total_429s,
                       SUM(api_5xx_count) as total_5xx,
                       CAST(AVG(avg_response_ms) AS INTEGER) as overall_avg_ms,
                       MAX(max_response_ms) as overall_max_ms,
                       SUM(total_bytes_downloaded) as total_bytes,
                       SUM(elapsed_seconds) as total_elapsed_s,
                       MIN(target_date) as earliest_date,
                       MAX(target_date) as latest_date
                   FROM build_metrics"""
            ).fetchone()
            if row is None or row[0] == 0:
                return {"message": "No build metrics recorded yet"}
            return dict(row)
