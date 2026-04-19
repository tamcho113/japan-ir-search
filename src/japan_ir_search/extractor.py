"""Extract and structure text from EDINET XBRL/HTML filing documents."""

from __future__ import annotations

import re
from typing import Any

from lxml import etree, html

from .config import SECTION_LABELS_JA


def extract_text_from_html(html_bytes: bytes, encoding: str = "utf-8") -> str:
    """Extract plain text from HTML, stripping all tags."""
    text = _parse_html_to_text(html_bytes, encoding)
    # Normalize whitespace while preserving paragraph breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def find_main_html_file(files: dict[str, bytes]) -> tuple[str, bytes] | None:
    """Find the main HTML/XBRL filing document from extracted zip contents.

    EDINET ZIPs have two formats in PublicDoc/:
      Old: single jpcrpXXX.htm (full filing in one file)
      New (iXBRL): 0000000_header_*.htm (cover page)
                   0101010_honbun_*.htm, 0102010_honbun_*.htm, ... (body split across files)

    For multi-file (honbun) format, we combine all body files in filename order
    so that the full text can be parsed and section-split downstream.
    """
    # Collect PublicDoc HTML files, separating headers from body
    honbun_files: list[tuple[str, bytes]] = []
    jpcrp_single: list[tuple[str, bytes]] = []
    other_candidates: list[tuple[str, bytes]] = []

    for name, content in files.items():
        lower = name.lower()
        if "/publicdoc/" not in lower:
            continue
        if not (lower.endswith(".htm") or lower.endswith(".html")):
            continue
        basename = lower.rsplit("/", 1)[-1]
        # Skip header/cover page files
        if "_header_" in basename:
            continue
        # Identify honbun (body part) files — named like 0101010_honbun_jpcrp*.htm
        if "_honbun_" in basename:
            honbun_files.append((name, content))
        elif "jpcrp" in basename:
            jpcrp_single.append((name, content))
        else:
            other_candidates.append((name, content))

    # New iXBRL format: combine all honbun files in order
    if honbun_files:
        sorted_honbun = sorted(honbun_files, key=lambda x: x[0])
        combined = b"\n".join(content for _, content in sorted_honbun)
        return ("(combined_honbun)", combined)

    # Old format: single jpcrp file
    if jpcrp_single:
        return max(jpcrp_single, key=lambda x: len(x[1]))

    # Fall back to largest non-header HTML in PublicDoc
    if other_candidates:
        return max(other_candidates, key=lambda x: len(x[1]))

    # Last resort: any non-header HTML file in the archive
    html_files = [
        (n, c) for n, c in files.items()
        if n.lower().endswith((".htm", ".html"))
        and "_header_" not in n.lower().rsplit("/", 1)[-1]
    ]
    if html_files:
        return max(html_files, key=lambda x: len(x[1]))
    return None


def split_into_sections(full_text: str) -> dict[str, str]:
    """Split plain text into named sections using 【】bracket headers.

    EDINET filings use standardized headers like 【事業の内容】, 【事業等のリスク】, etc.

    Returns:
        Dict mapping section key to extracted text (excluding full_text).
        Empty dict if no section headers were found.
    """
    # Find all matching section header positions
    found: list[tuple[int, int, str]] = []  # (match_start, match_end, key)

    for key, label in SECTION_LABELS_JA.items():
        escaped = re.escape(label)
        # Allow flexible whitespace between words
        flexible = escaped.replace(r"\ ", r"\s+").replace("、", r"[、,]\s*")
        pattern = r"【\s*" + flexible + r"\s*】"
        match = re.search(pattern, full_text)
        if match:
            found.append((match.start(), match.end(), key))

    if not found:
        return {}

    # Sort by position in text
    found.sort(key=lambda x: x[0])

    sections: dict[str, str] = {}
    for i, (_start, end, key) in enumerate(found):
        text_start = end
        # End at the next known section header, or end of text
        text_end = found[i + 1][0] if i + 1 < len(found) else len(full_text)
        section_text = full_text[text_start:text_end].strip()
        if len(section_text) > 500_000:
            section_text = section_text[:500_000] + "\n\n[...truncated...]"
        if section_text:
            sections[key] = section_text

    return sections


def _parse_html_to_text(html_bytes: bytes, encoding: str = "utf-8") -> str:
    """Parse HTML bytes to plain text, handling multi-document concatenation.

    When multiple iXBRL honbun files are concatenated, each has its own
    <?xml?> declaration and <html> root. We split on XML declarations and
    parse each fragment separately, then combine the text.
    """
    # Split on XML declarations to handle concatenated HTML documents
    chunks = re.split(rb"<\?xml[^?]*\?>", html_bytes)
    # Filter out empty chunks
    chunks = [c for c in chunks if c.strip()]

    if not chunks:
        return html_bytes.decode(encoding, errors="replace")

    text_parts: list[str] = []
    for chunk in chunks:
        text = _parse_single_html_chunk(chunk, encoding)
        text_parts.append(text)

    return "\n".join(text_parts)


def _parse_single_html_chunk(chunk: bytes, encoding: str = "utf-8") -> str:
    """Parse a single HTML/XHTML chunk to plain text.

    Tries lxml.html first (fast, lenient), falls back to lxml.etree
    for strict XHTML/iXBRL that html.fromstring() can't handle.
    """
    # Try lxml.html parser first (handles most HTML)
    try:
        doc = html.fromstring(chunk)
    except Exception:
        # Fallback: try XML/XHTML parser (handles strict iXBRL with namespaces)
        try:
            doc = etree.fromstring(chunk)
        except Exception:
            return chunk.decode(encoding, errors="replace")
    _strip_tags(doc)
    return _element_text_content(doc)


def _strip_tags(doc: etree._Element) -> None:
    """Remove script and style elements from a parsed document."""
    # Handle both with and without namespaces
    for tag in ("script", "style"):
        for el in doc.iter(tag, f"{{{_XHTML_NS}}}{tag}"):
            if el.getparent() is not None:
                el.getparent().remove(el)


_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _element_text_content(el: etree._Element) -> str:
    """Extract text content from an lxml element, handling both HTML and XML trees."""
    # lxml.html elements have .text_content(), etree elements don't always
    if hasattr(el, "text_content"):
        try:
            return el.text_content()
        except Exception:
            pass
    # Manual text extraction for etree-parsed XHTML
    return "".join(el.itertext())


def extract_sections(html_bytes: bytes, encoding: str = "utf-8") -> dict[str, str]:
    """Extract named sections from a securities report HTML.

    Parses HTML to plain text, then splits on 【section header】 markers.
    Handles both single-file and multi-file (concatenated honbun) input.

    Returns:
        Dict mapping section key to extracted text content.
        Always includes "full_text" key.
    """
    full_text = _parse_html_to_text(html_bytes, encoding)

    # Clean up common artifacts before section splitting
    full_text = _clean_text_artifacts(full_text)

    # Split into named sections
    sections = split_into_sections(full_text)

    # Always include full text (normalized whitespace, truncated)
    full_text_clean = re.sub(r"\s+", " ", full_text).strip()
    if len(full_text_clean) > 1_000_000:
        full_text_clean = full_text_clean[:1_000_000] + " [...truncated...]"
    sections["full_text"] = full_text_clean

    return sections


# Pattern: filename-like prefixes at the start of extracted text
# e.g., "有価証券報告書（通常方式）_20250526102706 " or "0000000_header_XXX.htm "
_FILENAME_PREFIX_RE = re.compile(
    r"^(?:\ufeff)?"  # optional BOM
    r"(?:"
    r"有価証券報告書[（(][^)）]*[)）]_\d{14}\s+"  # 有報(通常方式)_TIMESTAMP prefix
    r"|[0-9]+_(?:header|honbun)_[^\s]+\.htm\s+"  # 0000000_header_XXX.htm prefix
    r"|\ufeff"  # standalone BOM
    r")"
)


def _clean_text_artifacts(text: str) -> str:
    """Remove common artifacts from extracted text (BOM, filename prefixes)."""
    # Remove BOM
    text = text.lstrip("\ufeff")
    # Remove filename-like prefix at the start (repeated for multiple files)
    while True:
        m = _FILENAME_PREFIX_RE.match(text)
        if m:
            text = text[m.end():]
        else:
            break
    return text


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
