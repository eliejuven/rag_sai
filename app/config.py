import os

from dotenv import load_dotenv

load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_API_BASE = "https://api.mistral.ai/v1"
MISTRAL_EMBED_MODEL = "mistral-embed"
MISTRAL_CHAT_MODEL = "mistral-small-latest"

CHUNK_SIZE = 2000
CHUNK_OVERLAP = 400

SIMILARITY_TOP_K = 5
SIMILARITY_THRESHOLD = 0.7
