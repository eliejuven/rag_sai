"""Quick test for cvm_registry.lookup_company()"""
from app.scraper.cvm_registry import lookup_company

tests = [
    "Petrobras",
    "Ambev",
    "Vivo",
    "Vale",
    "Itau Unibanco",
    "Magazine Luiza",
    "Banco do Brasil",
    "XYZ_COMPANY_THAT_DOESNT_EXIST",
]

for q in tests:
    result = lookup_company(q)
    if result:
        print(f"✓ '{q}' → {result['trade_name'] or result['name']} | CNPJ: {result['cnpj']} | CD_CVM: {result['cd_cvm']}")
    else:
        print(f"✗ '{q}' → NOT FOUND")
