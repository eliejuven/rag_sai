# RAG Pipeline

A Retrieval-Augmented Generation (RAG) system that lets you upload PDF documents and ask questions about their content. Built with FastAPI and Mistral AI, with no external RAG or search libraries.

## System Design

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Chat UI (HTML/JS)                    │
│                     served at localhost:8000                 │
└──────────────┬──────────────────────────┬───────────────────┘
               │ POST /ingest             │ POST /query
               ▼                          ▼
┌──────────────────────┐   ┌──────────────────────────────────┐
│   Ingestion Pipeline │   │         Query Pipeline           │
│                      │   │                                  │
│  PDF Upload          │   │  Intent Detection (Mistral LLM)  │
│       ▼              │   │       │                          │
│  Text Extraction     │   │  ┌────┴────┐                     │
│  (PyMuPDF)           │   │  chat    search                  │
│       ▼              │   │  │         ▼                     │
│  Chunking            │   │  │    Query Transformation       │
│  (recursive split)   │   │  │    (Mistral LLM)              │
│       ▼              │   │  │         ▼                     │
│  Embedding           │   │  │    ┌────┴────┐                │
│  (Mistral API)       │   │  │    ▼         ▼                │
│       ▼              │   │  │  Semantic   Keyword           │
│  Store in            │   │  │  Search     Search            │
│  Vector Store        │   │  │  (cosine)   (BM25)            │
│  + BM25 Index        │   │  │    └────┬────┘                │
│                      │   │  │         ▼                     │
└──────────────────────┘   │  │    Reranking (RRF)            │
                           │  │         ▼                     │
                           │  │    Confidence Check           │
                           │  │    (similarity threshold)     │
                           │  │    ┌────┴────┐                │
                           │  │   low       high              │
                           │  │    │         ▼                │
                           │  │    │    RAG Generation        │
                           │  │    │    (Mistral LLM)         │
                           │  ▼    ▼         ▼                │
                           │  General    Grounded Answer      │
                           │  Knowledge  with Citations       │
                           └──────────────────────────────────┘
```

### Key Components

#### 1. Data Ingestion (`app/ingestion/`)

**PDF Parsing** (`pdf_parser.py`): Uses PyMuPDF to extract text page by page. Handles digital PDFs efficiently; skips pages with no text content.

**Text Chunking** (`chunker.py`): Splits extracted text into overlapping chunks using a recursive character splitter. The algorithm:
- Combines all pages into a single text while tracking page boundaries.
- Tries to split at natural boundaries in order of preference: paragraph (`\n\n`), line (`\n`), sentence (`. `), word (` `).
- Applies a configurable overlap (default 400 chars) between consecutive chunks to preserve context at boundaries.
- Chunk size of 2000 characters balances retrieval precision (small enough to be specific) with context preservation (large enough to contain complete ideas).

#### 2. Embeddings (`app/embeddings/`)

Calls Mistral's embedding API (`mistral-embed`) to convert text chunks into 1024-dimensional vectors. Handles batching automatically (16 texts per API call). The same model is used for both document chunks and user queries to ensure vectors live in the same semantic space.

#### 3. Search (`app/search/`)

**Semantic Search** (`vector_store.py`): In-memory vector store built with NumPy. Vectors are normalized at insertion time so search is a single matrix multiplication (dot product = cosine similarity). Uses `np.argpartition` for O(n) top-k selection.

**Keyword Search** (`keyword_search.py`): BM25 implementation from scratch. Scores documents using term frequency, inverse document frequency, and length normalization. Catches exact keyword matches that semantic search might underweight.

**Hybrid Reranking** (`reranker.py`): Merges semantic and keyword results using Reciprocal Rank Fusion (RRF). RRF is score-agnostic — it uses only ranks, which avoids the need to normalize different scoring scales. Formula: `RRF(d) = Σ 1/(k + rank)` with k=60.

#### 4. Query Processing (`app/query/`)

**Intent Detection** (`intent.py`): Uses Mistral LLM to classify whether a query needs document search ("search") or is casual conversation ("chat"). Prevents unnecessary retrieval for greetings and small talk.

**Query Transformation** (`transform.py`): Rewrites user queries to improve retrieval. Expands abbreviations, makes implicit context explicit, and rephrases for clarity. Example: "What's the ROI?" → "What is the return on investment mentioned in the documents?"

#### 5. Generation (`app/generation/`)

**LLM Client** (`llm.py`): Reusable wrapper around Mistral's chat completion API. Used by intent detection, query transformation, and answer generation.

**Prompt Templates** (`prompts.py`): RAG prompt injects retrieved chunks as numbered context with source info. The system prompt instructs the LLM to answer only from the provided context and cite sources using `[1]`, `[2]` notation.

#### 6. Confidence & Grounding

After retrieval, the system checks the best cosine similarity score against a configurable threshold (default 0.7):
- **Above threshold**: Answer is generated from document context (grounded), with citations and source references.
- **Below threshold**: Answer is generated from the LLM's general knowledge, clearly marked as "General knowledge" in the UI.

This avoids both hallucination (forcing RAG on irrelevant chunks) and unhelpful refusals (blocking all off-topic questions).

### Combining Semantic and Keyword Search

Semantic search excels at meaning ("car" matches "automobile") but can miss exact terms. Keyword search (BM25) catches precise matches ("EBITDA") but misses synonyms. Hybrid search with RRF reranking gives the best of both: results that rank high in either or both systems float to the top, without needing to normalize their different score scales.

## Project Structure

```
app/
├── main.py                  # FastAPI application entry point
├── config.py                # Configuration (API keys, thresholds)
├── models.py                # Pydantic request/response schemas
├── storage.py               # In-memory document and chunk storage
├── ingestion/
│   ├── pdf_parser.py        # PDF text extraction (PyMuPDF)
│   └── chunker.py           # Recursive text chunking with overlap
├── embeddings/
│   └── client.py            # Mistral embedding API client
├── search/
│   ├── vector_store.py      # NumPy cosine similarity search
│   ├── keyword_search.py    # BM25 keyword search
│   └── reranker.py          # Reciprocal Rank Fusion
├── query/
│   ├── intent.py            # Intent classification (search vs chat)
│   └── transform.py         # Query rewriting for better retrieval
├── generation/
│   ├── llm.py               # Mistral chat completion client
│   └── prompts.py           # RAG prompt templates
├── routers/
│   ├── ingest.py            # POST /ingest endpoint
│   └── query.py             # POST /query endpoint
└── static/
    └── index.html           # Chat UI (single-page HTML/JS)
```

## How to Run

### Prerequisites

- Python 3.12+
- A Mistral AI API key

### Setup

```bash
# Clone the repository
git clone https://github.com/eliejuven/rag_sai.git
cd rag_sai

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set your Mistral API key
echo "MISTRAL_API_KEY=your_key_here" > .env
```

### Run

```bash
source .venv/bin/activate
uvicorn app.main:app --port 8000
```

Open http://localhost:8000 in your browser. Upload PDFs using the button in the header, then ask questions in the chat.

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/ingest` | Upload one or more PDF files (multipart form) |
| `POST` | `/query` | Ask a question (JSON: `{"question": "..."}`) |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Interactive API documentation (Swagger) |

## Libraries Used

| Library | Version | Purpose |
|---------|---------|---------|
| [FastAPI](https://fastapi.tiangolo.com/) | 0.115.12 | Web framework |
| [Uvicorn](https://www.uvicorn.org/) | 0.34.2 | ASGI server |
| [PyMuPDF](https://pymupdf.readthedocs.io/) | 1.25.5 | PDF text extraction |
| [NumPy](https://numpy.org/) | 2.2.4 | Vector storage and cosine similarity |
| [httpx](https://www.python-httpx.org/) | 0.28.1 | Async HTTP client for Mistral API |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.1.0 | Environment variable management |
| [python-multipart](https://github.com/Kludex/python-multipart) | 0.0.20 | File upload support |

No external libraries are used for search, RAG, or vector storage.

## Design Decisions

**In-memory storage**: Documents, chunks, and vectors are stored in Python data structures (dict, list, NumPy array). This keeps the system simple and fast, and avoids external infrastructure dependencies. For production, these would be backed by a database and a persistent vector store.

**Single-file UI**: The chat interface is a single HTML file with embedded CSS and JavaScript, served by FastAPI's static file mount. This avoids the complexity of a separate frontend build step while still providing a clean, functional interface.

**Chunking strategy**: A chunk size of ~2000 characters with 400-character overlap was chosen to balance precision and context. Smaller chunks improve retrieval specificity but risk losing context; larger chunks preserve context but dilute the embedding signal. The recursive separator hierarchy ensures chunks break at natural text boundaries.

**Similarity threshold (0.7)**: Tuned empirically. Below this threshold, retrieved chunks are unlikely to be relevant, so the system answers from general knowledge instead of forcing a poor RAG response. This prevents both hallucination and unhelpful refusals.
