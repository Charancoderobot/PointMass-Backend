# Embedding Quickstart

This project now includes:

- `requirements.txt`
- `scripts/build_embeddings.py`

## 1) Create a virtual environment

From project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

## 2) Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 3) Build embeddings (local model, no API key)

```bash
python scripts/build_embeddings.py \
  --pdf college-physics-ap-courses-2e_-_WEB.pdf \
  --provider local \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --out data/embeddings/textbook_chunks_v1.jsonl
```

## 4) Optional: Build embeddings with OpenAI

```bash
export OPENAI_API_KEY="your_key_here"
python scripts/build_embeddings.py \
  --pdf college-physics-ap-courses-2e_-_WEB.pdf \
  --provider openai \
  --model text-embedding-3-small \
  --out data/embeddings/textbook_chunks_openai_v1.jsonl
```

On Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your_key_here"
python scripts/build_embeddings.py --provider openai --model text-embedding-3-small
```

## Output

The script writes JSONL rows like:

```json
{"id":"...","page":12,"chunk_index_on_page":1,"text":"...","embedding_model":"...","embedding":[...]}
```

Use this file as input for:

- PostgreSQL `pgvector` bulk load
- topic tagging/classification
- retrieval for hints and related problems
