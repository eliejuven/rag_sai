"""Test cvm_client.fetch_statements() on Petrobras."""
from app.scraper.cvm_client import fetch_statements

PETROBRAS_CNPJ = "33.000.167/0001-01"

print("Fetching Petrobras statements (DFP 2024, ITR 2025)...")
pages = fetch_statements(
    cnpj=PETROBRAS_CNPJ,
    company_name="PETROBRAS",
    dfp_years=[2024],
    itr_years=[2025],
)

print(f"\nTotal pages returned: {len(pages)}\n")
for page in pages:
    lines = page["text"].split("\n")
    header = "\n".join(lines[:6])
    print(f"--- Page {page['page_number']} ---")
    print(header)
    print(f"  ... ({len(lines)} lines total)\n")
