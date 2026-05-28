#!/usr/bin/env python3
"""
Parse a physics PDF, chunk text, and generate embeddings.

Outputs JSONL with one row per chunk:
{
  "id": "...",
  "page": 10,
  "chunk_index_on_page": 2,
  "text": "...",
  "embedding_model": "...",
  "embedding": [ ... ]
}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader
from tqdm import tqdm

from env_utils import load_local_env


@dataclass
class Chunk:
    page: int
    chunk_index_on_page: int
    text: str


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_words(text: str, chunk_size_words: int, overlap_words: int) -> Iterable[str]:
    words = text.split()
    if not words:
        return
    step = max(1, chunk_size_words - overlap_words)
    for i in range(0, len(words), step):
        part = words[i : i + chunk_size_words]
        if not part:
            continue
        yield " ".join(part)
        if i + chunk_size_words >= len(words):
            break


def extract_chunks(
    pdf_path: Path,
    chunk_size_words: int,
    overlap_words: int,
    page_start: int | None = None,
    page_end: int | None = None,
) -> list[Chunk]:
    reader = PdfReader(str(pdf_path))
    chunks: list[Chunk] = []
    for page_idx, page in enumerate(tqdm(reader.pages, desc="Extracting pages"), start=1):
        if page_start is not None and page_idx < page_start:
            continue
        if page_end is not None and page_idx > page_end:
            continue
        raw = page.extract_text() or ""
        cleaned = clean_text(raw)
        if not cleaned:
            continue
        for chunk_idx, chunk_text in enumerate(
            chunk_words(cleaned, chunk_size_words, overlap_words), start=1
        ):
            chunks.append(
                Chunk(
                    page=page_idx,
                    chunk_index_on_page=chunk_idx,
                    text=chunk_text,
                )
            )
    return chunks


def embed_local(chunks: list[Chunk], model_name: str) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    texts = [c.text for c in chunks]
    vectors = model.encode(texts, batch_size=32, show_progress_bar=True, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def embed_openai(chunks: list[Chunk], model_name: str) -> list[list[float]]:
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set.")

    vectors: list[list[float]] = []
    batch_size = 100
    for i in tqdm(range(0, len(chunks), batch_size), desc="Embedding"):
        batch = chunks[i : i + batch_size]
        inputs = [c.text for c in batch]
        resp = client.embeddings.create(model=model_name, input=inputs)
        vectors.extend([row.embedding for row in resp.data])
    return vectors


def embed_gemini(
    chunks: list[Chunk],
    model_name: str,
    output_dimensionality: int,
    max_requests_per_minute: int,
    batch_size: int,
    max_total_requests: int,
    out_path: Path | None = None,
    append_mode: bool = False,
) -> list[list[float]]:
    from google import genai
    from google.genai import errors
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.")

    client = genai.Client(api_key=api_key)

    vectors: list[list[float]] = []
    min_interval_seconds = 60.0 / float(max_requests_per_minute)
    next_allowed_at = 0.0
    request_count = 0
    already_written = 0

    out_file = None
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append_mode else "w"
        out_file = out_path.open(mode, encoding="utf-8")

    def persist_rows(batch_chunks: list[Chunk], batch_vectors: list[list[float]]) -> None:
        nonlocal already_written
        if out_file is None:
            return
        for c, emb in zip(batch_chunks, batch_vectors):
            row = {
                "id": str(uuid.uuid4()),
                "page": c.page,
                "chunk_index_on_page": c.chunk_index_on_page,
                "text": c.text,
                "embedding_model": model_name,
                "embedding": emb,
            }
            out_file.write(json.dumps(row, ensure_ascii=False) + "\n")
        out_file.flush()
        already_written += len(batch_chunks)

    def wait_for_slot() -> None:
        nonlocal next_allowed_at
        now = time.monotonic()
        if now < next_allowed_at:
            time.sleep(next_allowed_at - now)
        next_allowed_at = time.monotonic() + min_interval_seconds

    def ensure_budget() -> None:
        if request_count >= max_total_requests:
            raise RuntimeError(
                f"Reached max_total_requests={max_total_requests}. "
                f"Processed {len(vectors)} / {len(chunks)} chunks."
            )

    def request_with_backoff(contents: str | list[str]):
        nonlocal request_count
        backoff_seconds = 30
        while True:
            ensure_budget()
            wait_for_slot()
            try:
                result = client.models.embed_content(
                    model=model_name,
                    contents=contents,
                    config=types.EmbedContentConfig(output_dimensionality=output_dimensionality),
                )
                request_count += 1
                return result
            except (errors.ClientError, errors.ServerError) as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code not in (429, 503):
                    raise
                print(
                    f"{status_code} transient Gemini error. "
                    f"Sleeping {backoff_seconds}s before retrying..."
                )
                time.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 900)

    try:
        for i in tqdm(range(0, len(chunks), batch_size), desc="Embedding"):
            batch = chunks[i : i + batch_size]
            result = request_with_backoff([c.text for c in batch])

            # Some Gemini embedding models return a single aggregated embedding for
            # multi-input calls. When that happens, fall back to per-item requests.
            if len(result.embeddings) == 1 and len(batch) > 1:
                batch_vectors: list[list[float]] = []
                for c in batch:
                    single = request_with_backoff(c.text)
                    if len(single.embeddings) != 1:
                        raise RuntimeError(
                            f"Gemini single-item response mismatch: expected 1, got {len(single.embeddings)}"
                        )
                    [embedding_obj] = single.embeddings
                    batch_vectors.append(list(embedding_obj.values))
                vectors.extend(batch_vectors)
                persist_rows(batch, batch_vectors)
                continue

            if len(result.embeddings) != len(batch):
                raise RuntimeError(
                    f"Gemini embedding response count mismatch: expected {len(batch)}, got {len(result.embeddings)}"
                )

            batch_vectors = [list(embedding_obj.values) for embedding_obj in result.embeddings]
            vectors.extend(batch_vectors)
            persist_rows(batch, batch_vectors)
    finally:
        if out_file is not None:
            out_file.close()

    return vectors


def write_jsonl(chunks: list[Chunk], embeddings: list[list[float]], out_path: Path, model_name: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for c, emb in zip(chunks, embeddings):
            row = {
                "id": str(uuid.uuid4()),
                "page": c.page,
                "chunk_index_on_page": c.chunk_index_on_page,
                "text": c.text,
                "embedding_model": model_name,
                "embedding": emb,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(chunks: list[Chunk], embeddings: list[list[float]], out_path: Path, model_name: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        for c, emb in zip(chunks, embeddings):
            row = {
                "id": str(uuid.uuid4()),
                "page": c.page,
                "chunk_index_on_page": c.chunk_index_on_page,
                "text": c.text,
                "embedding_model": model_name,
                "embedding": emb,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def count_existing_rows(out_path: Path) -> int:
    if not out_path.exists():
        return 0
    with out_path.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build PDF chunk embeddings.")
    p.add_argument(
        "--pdf",
        default="college-physics-ap-courses-2e_-_WEB.pdf",
        help="Path to input PDF",
    )
    p.add_argument(
        "--out",
        default="data/embeddings/textbook_chunks_v1.jsonl",
        help="Output JSONL path",
    )
    p.add_argument(
        "--provider",
        choices=["local", "openai", "gemini"],
        default="local",
        help="Embedding provider",
    )
    p.add_argument(
        "--model",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="Embedding model name",
    )
    p.add_argument(
        "--output-dimensionality",
        type=int,
        default=1536,
        help="Output vector size for Gemini embeddings",
    )
    p.add_argument(
        "--max-requests-per-minute",
        type=int,
        default=90,
        help="Rate limit for Gemini API calls (keep below provider limit, e.g. 95)",
    )
    p.add_argument(
        "--gemini-batch-size",
        type=int,
        default=10,
        help="Number of chunks per Gemini embedding request",
    )
    p.add_argument(
        "--max-total-requests",
        type=int,
        default=1000,
        help="Maximum total Gemini requests to send in one run",
    )
    p.add_argument("--chunk-size-words", type=int, default=220)
    p.add_argument("--overlap-words", type=int, default=50)
    p.add_argument("--page-start", type=int, default=None, help="Start page (1-indexed, inclusive)")
    p.add_argument("--page-end", type=int, default=None, help="End page (1-indexed, inclusive)")
    p.add_argument(
        "--resume",
        action="store_true",
        help="Append to an existing JSONL by skipping already-written chunk rows",
    )
    return p.parse_args()


def main() -> None:
    load_local_env()
    args = parse_args()
    pdf_path = Path(args.pdf).resolve()
    out_path = Path(args.out).resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    chunks = extract_chunks(
        pdf_path,
        args.chunk_size_words,
        args.overlap_words,
        page_start=args.page_start,
        page_end=args.page_end,
    )
    if not chunks:
        raise RuntimeError("No chunks extracted from PDF.")

    existing_rows = count_existing_rows(out_path) if args.resume else 0
    if existing_rows > len(chunks):
        raise RuntimeError(
            f"Output file has {existing_rows} rows, but current extraction only produced {len(chunks)} chunks. "
            "Check your page range and chunking arguments."
        )
    if args.resume and existing_rows:
        print(f"Resuming from existing rows: {existing_rows}")
        chunks = chunks[existing_rows:]
    if not chunks:
        print(f"No remaining chunks to embed. Output already complete: {out_path}")
        return

    write_during_embedding = args.provider == "gemini"

    if args.provider == "local":
        model_name = args.model
        embeddings = embed_local(chunks, model_name)
    elif args.provider == "openai":
        model_name = args.model if args.model != "sentence-transformers/all-MiniLM-L6-v2" else "text-embedding-3-small"
        embeddings = embed_openai(chunks, model_name)
    else:
        model_name = args.model if args.model != "sentence-transformers/all-MiniLM-L6-v2" else "gemini-embedding-2"
        embeddings = embed_gemini(
            chunks,
            model_name=model_name,
            output_dimensionality=args.output_dimensionality,
            max_requests_per_minute=args.max_requests_per_minute,
            batch_size=args.gemini_batch_size,
            max_total_requests=args.max_total_requests,
            out_path=out_path,
            append_mode=args.resume and existing_rows > 0,
        )

    if not write_during_embedding:
        if args.resume and existing_rows:
            append_jsonl(chunks, embeddings, out_path, model_name=model_name)
        else:
            write_jsonl(chunks, embeddings, out_path, model_name=model_name)
    total_rows = existing_rows + len(chunks)
    print(f"Wrote {len(chunks)} chunk embeddings to: {out_path}")
    print(f"Total rows now in output: {total_rows}")


if __name__ == "__main__":
    main()
