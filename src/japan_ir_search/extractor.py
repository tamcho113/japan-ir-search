"""Extract and structure text from EDINET XBRL/HTML filing documents."""

from __future__ import annotations

import re
from typing import Any

from lxml import etree, html

from .config import SECTION_LABELS_JA


def extract_text_from_html(html_bytes: bytes, encoding: str = "utf-8") -> str:
    """Extract plain text from HTML, stripping all tags."""
    try:
        doc = html.fromstring(html_bytes.decode(encoding, errors="replace"))
    except Exception:
        return html_bytes.decode(encoding, errors="replace")
    # Remove script and style elements
    for el in doc.iter("script", "style"):
        el.getparent().remove(el)
    text = doc.text_content()
    # Normalize whitespace while preserving paragraph breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def find_main_html_file(files: dict[str, bytes]) -> tuple[str, bytes] | None:
    """Find the main HTML/XBRL filing document from extracted zip contents."""
    # EDINET XBRL zips typically have structure:
    #   XBRL/PublicDoc/jpcrpXXXX.htm (main filing)
    candidates: list[tuple[str, bytes]] = []
    for name, content in files.items():
        lower = name.lower()
        if "/publicdoc/" in lower and (lower.endswith(".htm") or lower.endswith(".html")):
            # Prefer the main document (jpcrp = 企業内容等開示)
            if "jpcrp" in lower:
                return (name, content)
            candidates.append((name, content))
    # Fall back to largest HTML file in PublicDoc
    if candidates:
        return max(candidates, key=lambda x: len(x[1]))
    # Last resort: any HTML file
    html_files = [
        (n, c) for n, c in files.items() if n.lower().endswith((".htm", ".html"))
    ]
    if html_files:
        return max(html_files, key=lambda x: len(x[1]))
    return None


def extract_sections(html_bytes: bytes, encoding: str = "utf-8") -> dict[str, str]:
    """Extract named sections from a securities report HTML.

    Looks for section headers matching known patterns and extracts text between them.

    Returns:
        Dict mapping section key to extracted text content.
    """
    try:
        doc = html.fromstring(html_bytes.decode(encoding, errors="replace"))
    except Exception:
        return {"full_text": html_bytes.decode(encoding, errors="replace")}

    # Remove script/style
    for el in doc.iter("script", "style"):
        if el.getparent() is not None:
            el.getparent().remove(el)

    full_text = doc.text_content()

    sections: dict[str, str] = {}

    # Build regex patterns for each section
    section_patterns: list[tuple[str, str]] = []
    for key, label in SECTION_LABELS_JA.items():
        # Escape for regex and allow flexible whitespace
        escaped = re.escape(label)
        # Allow whitespace/newlines between characters
        flexible = escaped.replace(r"\ ", r"\s*").replace("、", r"[、,]\s*")
        section_patterns.append((key, flexible))

    # Try to find each section in the full text
    for key, pattern in section_patterns:
        match = re.search(pattern, full_text)
        if match:
            start = match.end()
            # Find the end: next section header or end of text
            end = len(full_text)
            for other_key, other_pattern in section_patterns:
                if other_key == key:
                    continue
                other_match = re.search(other_pattern, full_text[start:])
                if other_match:
                    candidate_end = start + other_match.start()
                    if candidate_end < end:
                        end = candidate_end
            section_text = full_text[start:end].strip()
            # Limit section size to avoid storing massive blobs
            if len(section_text) > 500_000:
                section_text = section_text[:500_000] + "\n\n[...truncated...]"
            if section_text:
                sections[key] = section_text

    # Always include full text (truncated for storage)
    full_text_clean = re.sub(r"\s+", " ", full_text).strip()
    if len(full_text_clean) > 1_000_000:
        full_text_clean = full_text_clean[:1_000_000] + " [...truncated...]"
    sections["full_text"] = full_text_clean

    return sections


def extract_filing_metadata(doc_info: dict[str, Any]) -> dict[str, Any]:
    """Extract useful metadata from an EDINET document list entry."""
    return {
        "doc_id": doc_info.get("docID", ""),
        "edinet_code": doc_info.get("edinetCode", ""),
        "sec_code": doc_info.get("secCode", ""),
        "company_name": doc_info.get("filerName", ""),
        "doc_description": doc_info.get("docDescription", ""),
        "doc_type_code": doc_info.get("docTypeCode", ""),
        "filing_date": doc_info.get("submitDateTime", "")[:10] if doc_info.get("submitDateTime") else "",
        "period_start": doc_info.get("periodStart", ""),
        "period_end": doc_info.get("periodEnd", ""),
    }
