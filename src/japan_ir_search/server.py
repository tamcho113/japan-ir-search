"""MCP server exposing filing search tools."""

from __future__ import annotations

import os
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import SECTION_LABELS_JA
from .index import SearchIndex

mcp = FastMCP(
    "japan-ir-search",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "8000")),
    instructions="Full-text search over Japanese securities filings (EDINET). "
    "Search annual reports (有価証券報告書) text including risk factors, "
    "MD&A, business overview, and corporate governance sections.",
)

_index: SearchIndex | None = None


def _get_index() -> SearchIndex:
    global _index
    if _index is None:
        _index = SearchIndex()
        _index.initialize()
    return _index


@mcp.tool()
def search_filings(
    query: str,
    section: str | None = None,
    company: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """有価証券報告書のテキストを全文検索します。

    Args:
        query: 検索クエリ（例: "半導体 サプライチェーン リスク"）
        section: セクション指定（risk_factors, md_and_a, business_overview,
                 corporate_governance, financial_summary, full_text）
        company: 企業名フィルタ（部分一致）
        limit: 最大件数（デフォルト20）

    Returns:
        マッチした書類一覧（企業名、セクション、スニペット付き）
    """
    index = _get_index()
    t0 = time.monotonic()
    err: str | None = None
    results: list[dict[str, Any]] = []
    try:
        results = index.search(query, section=section, company=company, limit=limit)
        return results
    except Exception as e:
        err = type(e).__name__
        raise
    finally:
        index.record_usage(
            "search_filings",
            query=query,
            section=section,
            company=company,
            result_count=len(results),
            elapsed_ms=(time.monotonic() - t0) * 1000,
            error=err,
        )


@mcp.tool()
def get_filing_section(
    doc_id: str,
    section: str,
) -> dict[str, Any]:
    """書類の特定セクション全文を取得します。

    Args:
        doc_id: EDINET書類ID（例: "S100VWVY"）
        section: セクションキー（risk_factors, md_and_a, business_overview,
                 corporate_governance, financial_summary, full_text）

    Returns:
        セクションの全文テキストとメタデータ
    """
    index = _get_index()
    t0 = time.monotonic()
    err: str | None = None
    found = 0
    try:
        text = index.get_section_text(doc_id, section)
        info = index.get_filing_info(doc_id)
        if text is None or info is None:
            return {"error": f"Filing {doc_id} section {section} not found"}
        found = 1
        return {
            "doc_id": doc_id,
            "company_name": info["company_name"],
            "filing_date": info["filing_date"],
            "section": section,
            "section_label": SECTION_LABELS_JA.get(section, section),
            "text": text,
            "char_count": len(text),
        }
    except Exception as e:
        err = type(e).__name__
        raise
    finally:
        index.record_usage(
            "get_filing_section",
            query=doc_id,
            section=section,
            result_count=found,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            error=err,
        )


@mcp.tool()
def list_indexed_companies(
    query: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """インデックス済み企業の一覧を取得します。

    Args:
        query: 企業名フィルタ（部分一致、省略で全企業）
        limit: 最大件数

    Returns:
        企業一覧（EDINETコード、証券コード、企業名、書類数）
    """
    index = _get_index()
    t0 = time.monotonic()
    results: list[dict[str, Any]] = []
    err: str | None = None
    try:
        results = index.list_companies(query, limit=limit)
        return results
    except Exception as e:
        err = type(e).__name__
        raise
    finally:
        index.record_usage(
            "list_indexed_companies",
            query=query,
            result_count=len(results),
            elapsed_ms=(time.monotonic() - t0) * 1000,
            error=err,
        )


@mcp.tool()
def get_index_stats() -> dict[str, Any]:
    """検索インデックスの統計情報を取得します。

    Returns:
        インデックス済み書類数、企業数、総文字数等
    """
    index = _get_index()
    t0 = time.monotonic()
    try:
        return index.stats()
    finally:
        index.record_usage(
            "get_index_stats",
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


@mcp.tool()
def compare_sections(
    doc_id_1: str,
    doc_id_2: str,
    section: str = "risk_factors",
) -> dict[str, Any]:
    """2つの書類の同一セクションを比較します（例: 前期 vs 当期のリスク記述）。

    Args:
        doc_id_1: 比較元の書類ID
        doc_id_2: 比較先の書類ID
        section: セクションキー（デフォルト: risk_factors）

    Returns:
        両方のテキスト、文字数差分、新出キーワード、消失キーワード
    """
    index = _get_index()
    t0 = time.monotonic()
    err: str | None = None
    try:
        text1 = index.get_section_text(doc_id_1, section)
        text2 = index.get_section_text(doc_id_2, section)
        info1 = index.get_filing_info(doc_id_1)
        info2 = index.get_filing_info(doc_id_2)

        if not all([text1, text2, info1, info2]):
            return {"error": "One or both filings/sections not found"}

        # Simple keyword diff analysis
        words1 = set(_extract_keywords(text1))
        words2 = set(_extract_keywords(text2))
        new_keywords = sorted(words2 - words1)[:50]
        removed_keywords = sorted(words1 - words2)[:50]

        len1 = len(text1) if text1 else 0
        len2 = len(text2) if text2 else 0
        change_pct = ((len2 - len1) / len1 * 100) if len1 > 0 else 0

        return {
            "section": section,
            "section_label": SECTION_LABELS_JA.get(section, section),
            "filing_1": {
                "doc_id": doc_id_1,
                "company_name": info1["company_name"] if info1 else "",
                "filing_date": info1["filing_date"] if info1 else "",
                "char_count": len1,
            },
            "filing_2": {
                "doc_id": doc_id_2,
                "company_name": info2["company_name"] if info2 else "",
                "filing_date": info2["filing_date"] if info2 else "",
                "char_count": len2,
            },
            "char_count_change": len2 - len1,
            "char_count_change_pct": f"{change_pct:+.1f}%",
            "new_keywords": new_keywords,
            "removed_keywords": removed_keywords,
        }
    except Exception as e:
        err = type(e).__name__
        raise
    finally:
        index.record_usage(
            "compare_sections",
            query=f"{doc_id_1}|{doc_id_2}",
            section=section,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            error=err,
        )


def _extract_keywords(text: str | None, min_length: int = 2) -> list[str]:
    """Extract meaningful keywords from Japanese text (simple approach)."""
    if not text:
        return []
    import re
    # Extract katakana words and kanji compounds
    patterns = [
        r"[\u30A0-\u30FF]{2,}",           # Katakana (2+ chars)
        r"[\u4E00-\u9FFF]{2,}",           # Kanji compounds (2+ chars)
        r"[A-Za-z]{3,}",                   # English words (3+ chars)
    ]
    keywords: list[str] = []
    for pattern in patterns:
        keywords.extend(re.findall(pattern, text))
    return keywords


def run_server(transport: str = "stdio") -> None:
    """Start the MCP server.

    Args:
        transport: 'stdio', 'sse', or 'streamable-http'
    """
    mcp.run(transport=transport)
