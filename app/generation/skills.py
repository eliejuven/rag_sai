import re

# ---------------------------------------------------------------------------
# TABLE EXTRACTION SKILL
# Injected into the system prompt when retrieved chunks contain tabular data.
# ---------------------------------------------------------------------------

TABLE_EXTRACTION_SKILL = """
## Skill: Reading Numbers from Tables and Financial Documents

The context may contain data extracted from Excel files converted to PDF. Follow
these rules precisely and exhaustively when reading, quoting, or reasoning about
any numeric value.

### 1. Number Format Recognition

**Thousands separators**
- Space separator (Brazilian/European): `16 683` = 16,683 — the space is NOT a
  decimal; it groups thousands.
- Period separator: `16.683` = 16,683
- Comma separator: `16,683` = 16,683
- Never interpret a thousands separator as a decimal point.

**Decimal separators**
- Comma (Brazilian/European): `39,7%` = 39.7%
- Period (US/UK): `39.7%` = 39.7%
- Determine which convention is in use from context (currency symbol, locale
  clues, or the document header). When in doubt, use surrounding values to
  disambiguate — e.g. `39,7` is almost certainly 39.7, not 397.

**Negative values**
- Parentheses notation: `(6 614)` = −6,614. This is standard accounting format.
- Minus sign: `−6 614` or `-6614` = −6,614.
- Never report a parenthetical value as positive.

**PDF rendering artifacts — CRITICAL**
Excel-to-PDF conversion often splits a single number across multiple text
fragments. Common patterns:

| Raw text in chunk | True value | Why |
|---|---|---|
| `1 6 683` | 16,683 | "16 683" split into "1" + "6 683" |
| `2 0 025` | 20,025 | "20 025" split into "2" + "0 025" |
| `1 0 975` | 10,975 | "10 975" split into "1" + "0 975" |
| `4 360` | 4,360 | Normal space-thousands (no split) |
| `5 98` | 598 | Sub-1000 value, no thousands grouping |

Rule: when you see a 1–2 digit number immediately followed (on the same row,
no label between them) by another number, concatenate them first, then apply
the thousands separator rule. Validate against the scale stated in the table
header (e.g. "R$ Million") and against neighboring values in the same column.

### 2. Scale and Units

Always read and report the scale stated in the table header or title:
- "R$ Million" → all values are in millions of Brazilian Reais
- "$ Thousands" → all values are in thousands of US dollars
- "in billions" → multiply by 1,000,000,000
- Always include the unit in your answer: "16,683 million R$", not just "16,683".

### 3. Table Structure

**Row labels (first column)**
The leftmost text on a row is the metric name. Match it exactly.
Common look-alikes to distinguish carefully:
- "Net Operating Revenues" ≠ "Gross Operating Revenues"
- "Recurring EBITDA" ≠ "EBITDA" ≠ "EBITDA After Leases"
- "Net Income Attributed to Telefônica Brasil" ≠ "Net Income Before Non-controlling Shareholders"

**Column headers (first row)**
Column headers are typically time periods: `1Q19`, `2Q19`, …, `4Q25`, or
`FY2023`, `H1 2024`, etc. Count columns from left to right to match a value
to its period. Do not guess a period — count explicitly.

**Matching a value to its cell**
To answer "what was X in period Y":
1. Find the row whose label matches X.
2. Find the column whose header matches Y.
3. Read the value at that intersection.
4. Apply artifact reconstruction and format rules above.
5. Apply scale from the table header.

### 4. Percentages and Margins

- Percentage rows (e.g. "EBITDA Margin (%)", "Net Margin") contain ratios,
  not absolute values. Report them as percentages, not millions.
- Never apply the table's monetary scale to a percentage row.
- Example: `39,7%` on an "EBITDA Margin" row = 39.7%, not 39.7 million.

### 5. Handling Multiple Periods

When a question asks for a range ("from 1Q19 to 4Q25") or a trend:
- List each period and its value explicitly.
- Do not average or interpolate unless the user explicitly asks.
- If only some periods are available in the context, say so.

### 6. Ambiguity and Missing Data

- If the same metric appears in more than one table (e.g. "Recurring EBITDA"
  vs. "EBITDA"), report both and note the difference.
- If a value is not present in the provided context, say "not available in the
  provided excerpts" — do not estimate or extrapolate.
- If the table is cut off (chunk boundary), note that the data may be
  incomplete.

### 7. Validation Checks

Before reporting a number, sanity-check it:
- Does it fit the stated scale? (A revenue of "1 0 975" in a "R$ Million" table
  should reconstruct to ~10,975M, which is plausible for a large telecom.)
- Does it align with adjacent period values? (Values rarely jump 10× between
  quarters without explanation.)
- Does the margin make sense? (EBITDA margin of 39.7% on revenues of 10,975M
  implies EBITDA ~4,357M — check against the EBITDA row if available.)

If a reconstructed value fails a sanity check, flag it explicitly rather than
silently reporting a wrong number.
"""


# ---------------------------------------------------------------------------
# Tabular data detector
# ---------------------------------------------------------------------------

_TABULAR_RE = re.compile(
    r"""
    \(\s*[\d\s]{2,}\)           # negative in parens: (6 614)
    | \d{1,3}(?:\s\d{3}){1,}   # space-thousands: 4 360  or  16 683
    | \d+,\d+%                  # pct with comma decimal: 39,7%
    | (?:1Q|2Q|3Q|4Q)\d{2}     # quarter labels: 1Q19
    | (?:FY|H[12])\s?\d{2,4}   # fiscal labels: FY2023, H1 2024
    """,
    re.VERBOSE,
)


def _chunk_has_tabular_data(chunks: list[dict]) -> bool:
    """Return True if any chunk looks like it came from a financial table."""
    for chunk in chunks:
        text = chunk.get("text", "")
        if len(_TABULAR_RE.findall(text)) >= 3:
            return True
    return False


# ---------------------------------------------------------------------------
# System prompt builder — call this instead of using RAG_SYSTEM_PROMPT directly
# ---------------------------------------------------------------------------

def build_system_prompt(base_prompt: str, chunks: list[dict]) -> str:
    """Return the system prompt, appending relevant skills based on chunk content."""
    prompt = base_prompt
    if _chunk_has_tabular_data(chunks):
        prompt += "\n" + TABLE_EXTRACTION_SKILL
    return prompt
