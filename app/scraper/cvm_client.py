"""
CVM Financial Data Client

Downloads DFP (annual) and ITR (quarterly YTD) financial statement data
from the CVM public data portal for a given company CNPJ.

Sources:
  DFP: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/
  ITR: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/

Output: list of page dicts compatible with the existing chunker pipeline.
Each "page" is one statement type (income statement, balance sheet, cash flow)
for one reporting period, formatted as structured text.
"""

import io
import logging
import zipfile
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

DFP_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/"
ITR_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/"

CACHE_DIR = Path(__file__).parent.parent.parent / "data"
DFP_CACHE_DIR = CACHE_DIR / "dfp"
ITR_CACHE_DIR = CACHE_DIR / "itr"

# Statement types (minimal scope). Each entry: human label → (preferred csv suffix, fallbacks...)
# CVM file names inside ZIP: dfp_cia_aberta_{SUFFIX}_{YEAR}.csv
STATEMENT_TYPES = {
    "Demonstração de Resultado (DRE)": ["DRE_con", "DRE_ind"],
    "Balanço Patrimonial Ativo (BPA)": ["BPA_con", "BPA_ind"],
    "Balanço Patrimonial Passivo (BPP)": ["BPP_con", "BPP_ind"],
    "Demonstração de Fluxo de Caixa (DFC)": [
        "DFC_MD_con", "DFC_MI_con", "DFC_MD_ind", "DFC_MI_ind"
    ],
}

SCALE_MULTIPLIERS = {
    "MIL": 1_000,
    "UNIDADE": 1,
    "MILHÃO": 1_000_000,
    "BILHÃO": 1_000_000_000,
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _zip_cache_path(doc_type: str, year: int) -> Path:
    cache_dir = DFP_CACHE_DIR if doc_type == "dfp" else ITR_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{doc_type}_cia_aberta_{year}.zip"


def _download_zip(doc_type: str, year: int) -> Path:
    """Download a CVM ZIP for a given doc_type (dfp/itr) and year, cache locally."""
    cache_path = _zip_cache_path(doc_type, year)
    if cache_path.exists():
        logger.info("Using cached %s", cache_path.name)
        return cache_path

    base_url = DFP_BASE_URL if doc_type == "dfp" else ITR_BASE_URL
    url = f"{base_url}{doc_type}_cia_aberta_{year}.zip"
    logger.info("Downloading %s...", url)

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()

    cache_path.write_bytes(response.content)
    logger.info("Saved to %s (%.1f MB)", cache_path.name, len(response.content) / 1e6)
    return cache_path


# ---------------------------------------------------------------------------
# CSV extraction from ZIP
# ---------------------------------------------------------------------------

def _read_csv_from_zip(zip_path: Path, csv_name: str) -> pd.DataFrame | None:
    """Extract and read a single named CSV from a CVM ZIP archive."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            match = next((n for n in names if csv_name.lower() in n.lower()), None)
            if match is None:
                return None
            with zf.open(match) as f:
                return pd.read_csv(
                    io.TextIOWrapper(f, encoding="latin-1"),
                    sep=";",
                    dtype=str,
                )
    except Exception as e:
        logger.warning("Failed to read %s from %s: %s", csv_name, zip_path.name, e)
        return None


def _extract_company_data(
    zip_path: Path,
    doc_type: str,
    year: int,
    cnpj: str,
    suffixes: list[str],
) -> pd.DataFrame | None:
    """
    Try each suffix in order and return the first DataFrame that contains
    data for the given CNPJ.
    """
    prefix = doc_type  # "dfp" or "itr"
    for suffix in suffixes:
        csv_name = f"{prefix}_cia_aberta_{suffix}_{year}.csv"
        df = _read_csv_from_zip(zip_path, csv_name)
        if df is None:
            continue

        # Normalize CNPJ for comparison
        cnpj_clean = cnpj.replace(".", "").replace("/", "").replace("-", "")
        df["_cnpj_clean"] = df["CNPJ_CIA"].str.replace(r"[./-]", "", regex=True)
        filtered = df[df["_cnpj_clean"] == cnpj_clean].copy()

        if not filtered.empty:
            filtered["_suffix"] = suffix
            return filtered

    return None


# ---------------------------------------------------------------------------
# Data formatting
# ---------------------------------------------------------------------------

def _period_label(dt_fim: str, period_type: str) -> str:
    """Convert a CVM end-date string to a human-readable period label."""
    try:
        d = datetime.strptime(dt_fim.strip(), "%Y-%m-%d").date()
    except ValueError:
        return dt_fim

    if period_type == "dfp":
        return f"FY {d.year}"

    month = d.month
    year = d.year
    if month == 3:
        return f"3M {year} (Jan–Mar)"
    if month == 6:
        return f"6M {year} (Jan–Jun)"
    if month == 9:
        return f"9M {year} (Jan–Sep)"
    return f"{d.strftime('%Y-%m-%d')}"


def _format_statement(
    df: pd.DataFrame,
    statement_label: str,
    company_name: str,
    period_label: str,
) -> str:
    """Format a filtered statement DataFrame as structured text for the RAG."""
    # Keep only current period (ÚLTIMO = this year, PENÚLTIMO = prior year comparison)
    if "ORDEM_EXERC" in df.columns:
        df = df[df["ORDEM_EXERC"].str.strip().str.upper() == "ÚLTIMO"]

    if df.empty:
        return ""

    # Take the latest version per account, then sort by account code
    if "VERSAO" in df.columns:
        df = df.sort_values("VERSAO", ascending=False)
        df = df.drop_duplicates(subset=["CD_CONTA"], keep="first")
    if "CD_CONTA" in df.columns:
        df = df.sort_values("CD_CONTA")

    # Determine scale
    scale = "UNIDADE"
    if "ESCALA_MOEDA" in df.columns:
        scales = df["ESCALA_MOEDA"].dropna().unique()
        if len(scales) > 0:
            scale = scales[0].strip().upper()
    multiplier = SCALE_MULTIPLIERS.get(scale, 1)
    scale_label = f"R$ {scale.title()}"

    lines = [
        f"{statement_label}",
        f"Empresa: {company_name}",
        f"Período: {period_label}",
        f"Escala: {scale_label}",
        "-" * 60,
    ]

    for _, row in df.iterrows():
        cd = str(row.get("CD_CONTA", "")).strip()
        ds = str(row.get("DS_CONTA", "")).strip()
        vl_raw = str(row.get("VL_CONTA", "")).strip().replace(",", ".")
        try:
            value = float(vl_raw) * multiplier
            value_str = f"{value:,.0f}"
        except ValueError:
            value_str = vl_raw

        lines.append(f"{cd} | {ds} | {value_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_statements(
    cnpj: str,
    company_name: str,
    dfp_years: list[int] | None = None,
    itr_years: list[int] | None = None,
) -> list[dict]:
    """
    Fetch DFP (annual) and ITR (quarterly YTD) financial statements for a
    company from the CVM public data portal.

    Args:
        cnpj: Company CNPJ (e.g. "33.000.167/0001-01").
        company_name: Human-readable name for labeling (e.g. "PETROBRAS").
        dfp_years: List of years to fetch annual data for.
                   Defaults to [current_year - 1, current_year - 2].
        itr_years: List of years to fetch quarterly (YTD) data for.
                   Defaults to [current_year, current_year - 1].

    Returns:
        List of page dicts: [{"page_number": N, "text": "..."}]
        Compatible with the existing chunker pipeline.
    """
    current_year = date.today().year

    if dfp_years is None:
        dfp_years = [current_year - 1, current_year - 2]
    if itr_years is None:
        itr_years = [current_year, current_year - 1]

    pages: list[dict] = []
    page_num = 1

    for doc_type, years in [("dfp", dfp_years), ("itr", itr_years)]:
        for year in years:
            try:
                zip_path = _download_zip(doc_type, year)
            except httpx.HTTPStatusError as e:
                logger.warning("No CVM data for %s %d: %s", doc_type.upper(), year, e)
                continue
            except Exception as e:
                logger.error("Failed to download %s %d: %s", doc_type.upper(), year, e)
                continue

            for stmt_label, suffixes in STATEMENT_TYPES.items():
                df = _extract_company_data(zip_path, doc_type, year, cnpj, suffixes)
                if df is None or df.empty:
                    continue

                # Group by period (DT_FIM_EXERC) — one page per period
                period_col = "DT_FIM_EXERC"
                periods = df[period_col].dropna().unique() if period_col in df.columns else [""]

                for period in sorted(periods):
                    period_df = df[df[period_col] == period] if period else df
                    label = _period_label(period, doc_type)
                    text = _format_statement(period_df, stmt_label, company_name, label)

                    if text.strip():
                        pages.append({"page_number": page_num, "text": text})
                        page_num += 1

    return pages
