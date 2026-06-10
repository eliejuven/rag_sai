"""
CVM Formulário de Referência (FRE) Client

The Formulário de Referência is a comprehensive annual disclosure document
that every B3-listed company must file with CVM. It contains qualitative
information not available in the structured DFP/ITR CSV data:
  - Section 2: Management commentary (MD&A) — financial conditions, results,
    non-accounting measures (EBITDA, KPIs defined by management)
  - Section 4: Risk factors
  - Section 5: Risk management policies
  - Section 1: Business description, operating segments

How this works:
  1. Download the FRE metadata index CSV from dados.cvm.gov.br
     → This gives us a LINK_DOC URL per company per version
  2. Download the FRE ZIP from rad.cvm.gov.br using that URL
     → The ZIP contains a single large XML file
  3. The XML embeds each section as a base64-encoded .docx file
  4. We decode only the credit-relevant sections (Option B filtering)
  5. Extract text from each .docx with python-docx
  6. Return pages compatible with the existing chunker pipeline

Sources:
  Metadata: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/
  Documents: https://www.rad.cvm.gov.br/ENETCONSULTA/frmDownloadDocumento.aspx
"""

import base64
import io
import logging
import re
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URLs and cache paths
# ---------------------------------------------------------------------------

# The metadata index — same portal as DFP/ITR, same ZIP pattern
FRE_META_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/"

# The document download endpoint — different server (RAD/ENET system)
FRE_DOC_URL = "https://www.rad.cvm.gov.br/ENETCONSULTA/frmDownloadDocumento.aspx"

CACHE_DIR = Path(__file__).parent.parent.parent / "data"
FRE_META_CACHE = CACHE_DIR / "fre"          # stores metadata ZIPs
FRE_DOC_CACHE = CACHE_DIR / "fre" / "docs"  # stores per-company document ZIPs

# The RAD server blocks httpx (connection reset). requests works fine.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

# ---------------------------------------------------------------------------
# Credit-relevant sections
# Each key is the section number prefix found in the embedded filename.
# The value is a human-readable label used to tag extracted pages.
# EXCLUDED: 4.4 (148 pages of individual lawsuits), all of sections 6-12
# ---------------------------------------------------------------------------

CREDIT_SECTIONS: dict[str, str] = {
    "1.2":  "Principais atividades do emissor",
    "1.3":  "Segmentos operacionais",
    "1.6":  "Regulação estatal",
    "1.15": "Contratos relevantes",
    "2.1":  "Condições financeiras e patrimoniais",
    "2.2":  "Resultados operacional e financeiro",
    "2.5":  "Medições não contábeis (EBITDA, KPIs)",
    "2.8":  "Itens relevantes não evidenciados nas DFs",
    "2.10": "Planos de negócios",
    "4.1":  "Fatores de risco",
    "4.2":  "Principais fatores de risco",
    "4.3":  "Riscos de mercado",
    "4.7":  "Outras contingências relevantes",
    "5.1":  "Gerenciamento de riscos e riscos de mercado",
}

# CVM mandates the FRE XML schema for all listed companies.
# These tag names are part of the official schema — they identify each section
# with 100% reliability regardless of how the company named their files.
# (Filenames are free-text typed by each IR team and full of inconsistencies:
#  "1. 2 - JBS..." (space), "2. 2,1-" (comma+space), "5,5,1" (commas), etc.)
XML_TAG_TO_SECTION: dict[str, str] = {
    "AtividadesEmissorControladas":     "1.2",
    "InfoSegmentosOperacionais":        "1.3",
    "EfeitosRegulacaoEstatal":          "1.6",
    "ContratosRelevantes":              "1.15",
    "CondicoesFinanceirasPatrimoniais": "2.1",
    "ResultadosOperFinanceiros":        "2.2",
    "MedicoesNaoContabeis":             "2.5",
    "ItensRelevantesNaoEvidenciadosDF": "2.8",
    "PlanoNegocios":                    "2.10",
    "DescricaoFatoresRisco":            "4.1",
    "Descricao5PrincipaisFatoresRisco": "4.2",
    "DescricaoRiscosMercado":           "4.3",
    "ContingenciasRelevantes":          "4.7",
    "DescricaoGerenciamentoRiscos":     "5.1",
}


# ---------------------------------------------------------------------------
# Step 1 — Metadata index: find LINK_DOC for a company
# ---------------------------------------------------------------------------

def _meta_cache_path(year: int) -> Path:
    """Local path for the FRE metadata ZIP for a given year."""
    FRE_META_CACHE.mkdir(parents=True, exist_ok=True)
    return FRE_META_CACHE / f"fre_cia_aberta_{year}.zip"


def _download_meta_zip(year: int) -> Path:
    """
    Download the FRE metadata index ZIP from dados.cvm.gov.br if not cached.
    This ZIP contains fre_cia_aberta_{year}.csv with one row per filing version.
    """
    cache_path = _meta_cache_path(year)
    if cache_path.exists():
        logger.info("Using cached FRE metadata: %s", cache_path.name)
        return cache_path

    url = f"{FRE_META_URL}fre_cia_aberta_{year}.zip"
    logger.info("Downloading FRE metadata index: %s", url)

    # This endpoint (dados.cvm.gov.br) works fine with httpx, but we use
    # requests here for consistency since fre_doc also needs requests.
    response = requests.get(url, headers=_HEADERS, timeout=60)
    response.raise_for_status()

    cache_path.write_bytes(response.content)
    logger.info("Saved FRE metadata (%s)", cache_path.name)
    return cache_path


def get_latest_fre_link(cnpj: str, year: int) -> tuple[str, int] | None:
    """
    Find the download URL and version number of the latest FRE filing
    for a company in a given year.

    Returns (link_doc_url, version) or None if not found.
    """
    zip_path = _download_meta_zip(year)

    # Read the metadata CSV from inside the ZIP
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = f"fre_cia_aberta_{year}.csv"
        with zf.open(csv_name) as f:
            df = pd.read_csv(
                io.TextIOWrapper(f, encoding="latin-1"),
                sep=";",
                dtype=str,
            ).fillna("")

    # Normalize CNPJ to digits-only for reliable comparison
    # "33.000.167/0001-01" → "33000167000101"
    cnpj_clean = re.sub(r"[./-]", "", cnpj)
    df["_cnpj_clean"] = df["CNPJ_CIA"].str.replace(r"[./-]", "", regex=True)

    company_rows = df[df["_cnpj_clean"] == cnpj_clean].copy()
    if company_rows.empty:
        logger.warning("No FRE found for CNPJ %s in year %d", cnpj, year)
        return None

    # VERSAO is a string like "1", "2", ..., "27". Sort numerically.
    company_rows["_versao_int"] = pd.to_numeric(
        company_rows["VERSAO"], errors="coerce"
    ).fillna(0).astype(int)

    # Take the row with the highest version number — the most recent filing
    latest = company_rows.sort_values("_versao_int", ascending=False).iloc[0]
    return latest["LINK_DOC"], int(latest["_versao_int"])


# ---------------------------------------------------------------------------
# Step 2 — Document download: get the FRE ZIP from rad.cvm.gov.br
# ---------------------------------------------------------------------------

def _doc_cache_path(cnpj: str, year: int, version: int) -> Path:
    """
    Local path for a specific company FRE document ZIP.
    Includes version in the filename so that if a new version is filed,
    the old one is kept alongside it (useful for audit trail).
    """
    FRE_DOC_CACHE.mkdir(parents=True, exist_ok=True)
    cnpj_clean = re.sub(r"[./-]", "", cnpj)
    return FRE_DOC_CACHE / f"{cnpj_clean}_{year}_v{version}.zip"


def _download_fre_doc(link_url: str, cnpj: str, year: int, version: int) -> Path:
    """
    Download the FRE document ZIP from rad.cvm.gov.br.

    The LINK_DOC in the metadata CSV uses http://, but the server requires
    HTTPS. We force the URL to HTTPS before requesting. We also use a
    Session so the server receives consistent headers across any redirects.
    """
    cache_path = _doc_cache_path(cnpj, year, version)
    if cache_path.exists():
        logger.info("Using cached FRE document: %s", cache_path.name)
        return cache_path

    # Force HTTPS — the CSV stores http:// but the server requires https://
    https_url = link_url.replace("http://", "https://", 1)

    logger.info("Downloading FRE document from RAD CVM (version %d)...", version)

    # Use a Session so headers are sent consistently on all requests
    # including any internal redirects the server may trigger
    session = requests.Session()
    session.headers.update(_HEADERS)
    response = session.get(https_url, timeout=120)
    response.raise_for_status()

    # Sanity check: ZIP files always start with PK (bytes 0x50 0x4B)
    if not response.content[:2] == b"PK":
        raise ValueError(
            f"Expected ZIP response from RAD CVM but got: "
            f"{response.content[:20]!r}"
        )

    cache_path.write_bytes(response.content)
    logger.info("Saved FRE document: %s", cache_path.name)
    return cache_path


# ---------------------------------------------------------------------------
# Step 3 — XML parsing: extract credit-relevant sections
# ---------------------------------------------------------------------------

def _extract_sections_from_xml(xml_bytes: bytes) -> list[tuple[str, bytes]]:
    """
    Extract credit-relevant sections by matching CVM-standardized XML tags.

    The CVM mandates the FRE XML schema — every company uses the same tag
    names regardless of how they name their embedded files. We search directly
    for the semantic tag (e.g. <DescricaoFatoresRisco>) and extract the
    base64 content inside it, bypassing the filename entirely.

    This handles all observed IR-team filename inconsistencies:
      Petrobras:  "2.1_1T25_Certificacao.docx"
      Telefônica: "4.01.pdf"           (zero-padded)
      Vale:       "FRE 2025 item 1.3.pdf" (text prefix)
      JBS:        "1. 2 - JBS..."      (space after dot)
      JBS:        "5,5,1 - JBS..."     (commas instead of dots)

    Returns a list of (section_number, pdf_bytes) tuples.
    """
    results = []

    for xml_tag, section_num in XML_TAG_TO_SECTION.items():
        # Match the opening tag, then find the NomeArquivoPdf (for logging)
        # and ImagemObjetoArquivoPdf (the actual content) inside it.
        pattern = (
            rb"<" + xml_tag.encode() + rb">"
            rb".*?<NomeArquivoPdf>(.*?)</NomeArquivoPdf>"
            rb".*?<ImagemObjetoArquivoPdf>(.*?)</ImagemObjetoArquivoPdf>"
        )
        m = re.search(pattern, xml_bytes, re.DOTALL)
        if not m:
            logger.debug("Section %s (<%s>) not found in XML", section_num, xml_tag)
            continue

        filename = m.group(1).decode("utf-8", errors="replace").strip()
        b64_content = m.group(2)

        try:
            pdf_bytes = base64.b64decode(b64_content.strip())
        except Exception as e:
            logger.warning("Failed to decode section %s (%s): %s", section_num, filename, e)
            continue

        results.append((section_num, pdf_bytes))
        logger.info("  Extracted section %s (%s) via <%s>",
                    section_num, CREDIT_SECTIONS[section_num], xml_tag)

    return results


# ---------------------------------------------------------------------------
# Step 4 — PDF text extraction
# The embedded files are PDFs (CVM converts .docx to PDF before storing).
# We use pdfplumber — same library used by the existing PDF parser.
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(pdf_bytes: bytes, section_label: str) -> str:
    """
    Extract clean text from a PDF using pdfplumber.

    Each section is a separate embedded PDF. We extract all pages,
    join them, and prepend the section label as a header so the LLM
    always knows which section of the FRE it is reading.
    """
    parts = [section_label, "-" * 60]

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            # extract_words reconstructs reading order from character positions,
            # which handles multi-column layouts better than extract_text()
            words = page.extract_words(x_tolerance=5, y_tolerance=3)
            if not words:
                continue

            # Group words into lines by their vertical position (top coordinate)
            # Words on the same line have nearly identical `top` values
            lines: dict[float, list[str]] = {}
            for word in words:
                # Round to nearest integer to group words on the same line
                line_key = round(word["top"])
                lines.setdefault(line_key, []).append(word["text"])

            # Sort lines top-to-bottom, join words in each line with a space
            for key in sorted(lines):
                line_text = " ".join(lines[key]).strip()
                if line_text:
                    parts.append(line_text)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fre_sections(
    cnpj: str,
    company_name: str,
    year: int | None = None,
) -> list[dict]:
    """
    Fetch credit-relevant sections from the Formulário de Referência for
    a given company.

    Args:
        cnpj:         Company CNPJ (e.g. "33.000.167/0001-01")
        company_name: Human-readable name for labeling (e.g. "PETROBRAS")
        year:         Reference year of the FRE. Defaults to current_year - 1
                      (the most recent complete annual filing).

    Returns:
        List of page dicts: [{"page_number": N, "text": "..."}]
        Compatible with the existing chunker pipeline.
        Each page is one credit-relevant section.
    """
    if year is None:
        year = date.today().year - 1

    # Step 1 — Find the download URL
    result = get_latest_fre_link(cnpj, year)
    if result is None:
        logger.warning("No FRE available for %s (%s) year %d", company_name, cnpj, year)
        return []
    link_url, version = result
    logger.info("Found FRE for %s: year=%d version=%d", company_name, year, version)

    # Step 2 — Download the document ZIP
    doc_zip_path = _download_fre_doc(link_url, cnpj, year, version)

    # Step 3 — Open ZIP and get the XML bytes
    with zipfile.ZipFile(doc_zip_path) as zf:
        # Find the main XML file (the one that is not FormularioCadastral.xml)
        xml_names = [n for n in zf.namelist() if n.endswith(".xml") and "FRE" in n]
        if not xml_names:
            # Fallback: take the largest XML file
            xml_names = sorted(
                [n for n in zf.namelist() if n.endswith(".xml")],
                key=lambda n: zf.getinfo(n).file_size,
                reverse=True,
            )
        if not xml_names:
            logger.error("No XML found in FRE ZIP for %s", company_name)
            return []

        xml_bytes = zf.read(xml_names[0])
        logger.info("Parsing FRE XML (%s, %.1f MB)", xml_names[0], len(xml_bytes) / 1e6)

    # Step 4 — Extract credit-relevant sections from XML
    sections = _extract_sections_from_xml(xml_bytes)
    if not sections:
        logger.warning("No credit-relevant sections found in FRE for %s", company_name)
        return []

    # Step 5 — Extract text from each PDF and build pages
    pages = []
    page_num = 1

    # Sort by section number so pages are in logical order (1.x, 2.x, 4.x, 5.x)
    sections.sort(key=lambda x: [int(p) for p in x[0].split(".")])

    for section_num, pdf_bytes in sections:
        section_description = CREDIT_SECTIONS[section_num]
        header = (
            f"Formulário de Referência — {company_name}\n"
            f"Seção {section_num}: {section_description}\n"
            f"Ano de referência: {year}"
        )

        try:
            text = _extract_text_from_pdf(pdf_bytes, header)
        except Exception as e:
            logger.warning("Failed to extract text from section %s: %s", section_num, e)
            continue

        if text.strip():
            pages.append({
                "page_number": page_num,
                "text": text,
                "section": section_num,
                "section_label": section_description,
            })
            page_num += 1

    logger.info(
        "FRE extraction complete for %s: %d sections, %d pages",
        company_name, len(sections), len(pages),
    )
    return pages
