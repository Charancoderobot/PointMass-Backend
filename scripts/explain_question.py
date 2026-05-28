#!/usr/bin/env python3
"""
Ask a single question, retrieve the top 5 matching textbook chunks, and return
a grounded 5-sentence explanation using Gemini.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from env_utils import load_local_env


EMBEDDING_MODEL = "gemini-embedding-2"
GENERATION_MODEL = "gemini-2.5-flash"
OUTPUT_DIMENSIONALITY = 1536
TOP_K = 5


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Explain a physics question using retrieved textbook chunks.")
    p.add_argument("question", nargs="?", help="Question to answer")
    return p.parse_args()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set.")
    return value


def embed_query(question: str) -> list[float]:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.")

    client = genai.Client(api_key=api_key)
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=question,
        config=types.EmbedContentConfig(output_dimensionality=OUTPUT_DIMENSIONALITY),
    )
    if len(result.embeddings) != 1:
        raise RuntimeError(f"Expected 1 query embedding, got {len(result.embeddings)}")
    return list(result.embeddings[0].values)


def as_vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def retrieve_chunks(db_url: str, question_embedding: list[float]) -> list[tuple]:
    sql = """
        select
          tc.id,
          tc.page_start,
          tc.page_end,
          tc.clean_text,
          1 - (tce.embedding <=> %s::vector) as similarity
        from textbook_chunk_embeddings tce
        join textbook_chunks tc on tc.id = tce.chunk_id
        where tce.embedding_model = %s
        order by tce.embedding <=> %s::vector
        limit %s
    """
    query_vector = as_vector_literal(question_embedding)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (query_vector, EMBEDDING_MODEL, query_vector, TOP_K))
            return cur.fetchall()


def generate_explanation(question: str, chunks: list[tuple]) -> str:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.")

    client = genai.Client(api_key=api_key)

    context_parts: list[str] = []
    for index, (_, page_start, page_end, text, similarity) in enumerate(chunks, start=1):
        context_parts.append(
            f"Source {index} | pages {page_start}-{page_end} | similarity {similarity:.4f}\n{text}"
        )
    context = "\n\n".join(context_parts)

    prompt = f"""You are answering a physics study question using only the retrieved textbook excerpts below.

Question:
{question}

Retrieved excerpts:
{context}

Write exactly 5 sentences.
Keep the explanation clear and accurate for a student.
Use only the retrieved material; do not introduce outside facts.
If the excerpts are incomplete, say so briefly within the explanation instead of guessing.
"""

    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty explanation.")
    return text


def main() -> None:
    load_local_env()
    args = parse_args()
    question = args.question
    if not question:
        question = input("Question: ").strip()

    if not question:
        raise RuntimeError("No question provided.")

    db_url = require_env("DATABASE_URL")
    embedding = embed_query(question)
    chunks = retrieve_chunks(db_url, embedding)
    if not chunks:
        raise RuntimeError("No matching chunks found in the database.")

    explanation = generate_explanation(question, chunks)
    print(explanation)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
