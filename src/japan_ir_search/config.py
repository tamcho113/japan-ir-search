"""Configuration and constants."""

from __future__ import annotations

import os
from pathlib import Path

EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
EDINET_API_KEY = os.environ.get("EDINET_API_KEY", "")

DATA_DIR = Path(os.environ.get("JAPAN_IR_SEARCH_DATA", Path.home() / ".japan-ir-search"))
DB_PATH = DATA_DIR / "filings.db"

# EDINET document type codes — HIGH value (rich narrative text)
DOC_TYPE_ANNUAL_REPORT = "120"           # 有価証券報告書
DOC_TYPE_AMENDED_ANNUAL = "130"           # 訂正有価証券報告書
DOC_TYPE_QUARTERLY_REPORT = "140"         # 四半期報告書 (abolished 2024, historical)
DOC_TYPE_AMENDED_QUARTERLY = "150"        # 訂正四半期報告書
DOC_TYPE_SEMIANNUAL_REPORT = "160"        # 半期報告書
DOC_TYPE_AMENDED_SEMIANNUAL = "170"       # 訂正半期報告書
DOC_TYPE_EXTRAORDINARY = "180"            # 臨時報告書
DOC_TYPE_AMENDED_EXTRAORDINARY = "190"    # 訂正臨時報告書

# MEDIUM value (structured but useful)
DOC_TYPE_INTERNAL_CONTROL = "235"         # 内部統制報告書
DOC_TYPE_AMENDED_INTERNAL_CONTROL = "236" # 訂正内部統制報告書
DOC_TYPE_TOB_FILING = "240"               # 公開買付届出書
DOC_TYPE_AMENDED_TOB_FILING = "250"       # 訂正公開買付届出書
DOC_TYPE_TOB_REPORT = "270"               # 公開買付報告書
DOC_TYPE_AMENDED_TOB_REPORT = "280"       # 訂正公開買付報告書
DOC_TYPE_TOB_OPINION = "290"              # 意見表明報告書
DOC_TYPE_AMENDED_TOB_OPINION = "300"      # 訂正意見表明報告書
DOC_TYPE_LARGE_SHAREHOLDING = "350"       # 大量保有報告書
DOC_TYPE_AMENDED_LARGE_SHAREHOLDING = "360"  # 訂正大量保有報告書

# All indexable types
INDEXABLE_DOC_TYPES_HIGH = {
    DOC_TYPE_ANNUAL_REPORT, DOC_TYPE_AMENDED_ANNUAL,
    DOC_TYPE_QUARTERLY_REPORT, DOC_TYPE_AMENDED_QUARTERLY,
    DOC_TYPE_SEMIANNUAL_REPORT, DOC_TYPE_AMENDED_SEMIANNUAL,
    DOC_TYPE_EXTRAORDINARY, DOC_TYPE_AMENDED_EXTRAORDINARY,
}
INDEXABLE_DOC_TYPES_MEDIUM = {
    DOC_TYPE_INTERNAL_CONTROL, DOC_TYPE_AMENDED_INTERNAL_CONTROL,
    DOC_TYPE_TOB_FILING, DOC_TYPE_AMENDED_TOB_FILING,
    DOC_TYPE_TOB_REPORT, DOC_TYPE_AMENDED_TOB_REPORT,
    DOC_TYPE_TOB_OPINION, DOC_TYPE_AMENDED_TOB_OPINION,
    DOC_TYPE_LARGE_SHAREHOLDING, DOC_TYPE_AMENDED_LARGE_SHAREHOLDING,
}
INDEXABLE_DOC_TYPES_ALL = INDEXABLE_DOC_TYPES_HIGH | INDEXABLE_DOC_TYPES_MEDIUM

# Sections we extract from annual reports
SECTIONS = [
    "business_overview",       # 事業の内容
    "risk_factors",            # 事業等のリスク
    "md_and_a",                # 経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析
    "corporate_governance",    # コーポレート・ガバナンスの状況等
    "financial_summary",       # 経理の状況
]

SECTION_LABELS_JA: dict[str, str] = {
    "business_overview": "事業の内容",
    "risk_factors": "事業等のリスク",
    "md_and_a": "経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析",
    "corporate_governance": "コーポレート・ガバナンスの状況等",
    "financial_summary": "経理の状況",
}
