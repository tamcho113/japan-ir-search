"""SQLite FTS5 full-text search index for filing documents."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from .config import DB_PATH

_SCHEMA_VERSION = 1

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

-- FTS5 virtual table for full-text search
-- tokenize: trigram works well for CJK text (character n-gram matching)
CREATE VIRTUAL TABLE IF NOT EXISTS filings_fts USING fts5(
    doc_id UNINDEXED,
    company_name,
    section_key UNINDEXED,
    section_text,
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
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        self._ensure_dir()
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create tables and FTS index if they don't exist."""
        with self._connect() as conn:
            conn.executescript(_CREATE_TABLES)
            # Check/set schema version
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
            conn.commit()

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
            # Remove old sections & FTS entries
            conn.execute("DELETE FROM filing_sections WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM filings_fts WHERE doc_id = ?", (doc_id,))

            company_name = metadata.get("company_name", "")
            for section_key, text in sections.items():
                if not text.strip():
                    continue
                conn.execute(
                    """INSERT INTO filing_sections (doc_id, section_key, section_text, char_count)
                       VALUES (?, ?, ?, ?)""",
                    (doc_id, section_key, text, len(text)),
                )
                conn.execute(
                    """INSERT INTO filings_fts (doc_id, company_name, section_key, section_text)
                       VALUES (?, ?, ?, ?)""",
                    (doc_id, company_name, section_key, text),
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

        Args:
            query: Search query (FTS5 syntax supported).
            section: Limit to a specific section key (e.g., "risk_factors").
            company: Filter by company name (partial match).
            limit: Max results.
            snippet_size: Approximate snippet character length.

        Returns:
            List of dicts with filing metadata, section key, and text snippet.
        """
        with self._connect() as conn:
            # Build FTS5 query
            fts_query = query
            where_clauses: list[str] = []
            params: list[Any] = [fts_query]

            if section:
                where_clauses.append("fts.section_key = ?")
                params.append(section)

            if company:
                where_clauses.append("f.company_name LIKE ?")
                params.append(f"%{company}%")

            where_sql = ""
            if where_clauses:
                where_sql = "AND " + " AND ".join(where_clauses)

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
                    fts.section_key,
                    snippet(filings_fts, 3, '【', '】', '...', 32) AS snippet,
                    rank
                FROM filings_fts fts
                JOIN filings f ON f.doc_id = fts.doc_id
                WHERE filings_fts MATCH ?
                {where_sql}
                ORDER BY rank
                LIMIT ?
            """
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

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
        with self._connect() as conn:
            filings_count = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
            sections_count = conn.execute("SELECT COUNT(*) FROM filing_sections").fetchone()[0]
            companies_count = conn.execute(
                "SELECT COUNT(DISTINCT edinet_code) FROM filings"
            ).fetchone()[0]
            total_chars = conn.execute(
                "SELECT COALESCE(SUM(char_count), 0) FROM filing_sections"
            ).fetchone()[0]
            return {
                "filings_indexed": filings_count,
                "sections_indexed": sections_count,
                "companies_indexed": companies_count,
                "total_characters": total_chars,
                "db_path": self._db_path,
            }

    def has_filing(self, doc_id: str) -> bool:
        """Check if a document is already indexed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM filings WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            return row is not None
