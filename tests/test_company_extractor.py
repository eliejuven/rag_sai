"""Test company extractor on various question types."""
import asyncio
from app.query.company_extractor import extract_company

questions = [
    # Standard names
    "Qual foi o EBITDA da Petrobras em 2024?",
    "Me mostre o balanço patrimonial da Vale no último trimestre.",
    # Brand aliases
    "Quanto a Vivo lucrou no terceiro trimestre de 2025?",
    "Qual é a receita do Magalu em 2024?",
    "Como está o endividamento do Pão de Açúcar?",
    "Me fala sobre o Itaú no primeiro semestre de 2025.",
    # Tickers
    "O que aconteceu com PETR4 no Q2 2024?",
    "Qual é a margem líquida da ABEV3?",
    # No company
    "O que é EBITDA?",
    "Como calcular margem bruta?",
    "Olá, tudo bem?",
    # Multiple companies (should return the primary one)
    "Compare a receita da Petrobras com a Vale em 2024.",
]

async def main():
    for q in questions:
        result = await extract_company(q)
        print(f"Q: {q[:60]:<60} → {result}")

asyncio.run(main())
