import sys
from app.ingestion.pdf_parser import extract_text_from_pdf

if len(sys.argv) < 2:
    print("Usage: python3 debug_pdf.py yourfile.pdf")
    sys.exit(1)

with open(sys.argv[1], "rb") as f:
    pdf_bytes = f.read()

pages = extract_text_from_pdf(pdf_bytes)
for page in pages[:2]:
    print(f"\n=== PAGE {page['page_number']} (first 800 chars) ===")
    print(page["text"][:800])
