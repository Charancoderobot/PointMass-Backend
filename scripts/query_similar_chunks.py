#!/usr/bin/env python3
"""
Embed a query with Gemini and search similar textbook chunks in PostgreSQL.
"""

from __future__ import annotations

import argparse
import os

import psycopg

from env_utils import load_local_env


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Search similar textbook chunks with Gemini + pgvector.")
    p.add_argument("--db-url", required=True, help="PostgreSQL URL")
    p.add_argument("--query", required=True, help="Natural-language search query")
    p.add_argument("--model", default="gemini-embedding-2", help="Gemini embedding model")
    p.add_argument("--output-dimensionality", type=int, default=1536, help="Embedding dimension")
    p.add_argument("--top-k", type=int, default=5, help="Number of matches to return")
    p.add_argument("--embedding-model-filter", default=None, help="Optional filter for stored embedding_model")
    return p.parse_args()


def embed_query(text: str, model_name: str, output_dimensionality: int) -> list[float]:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.")

    client = genai.Client(api_key=api_key)
    result = client.models.embed_content(
        model=model_name,
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=output_dimensionality),
    )
    if len(result.embeddings) != 1:
        raise RuntimeError(f"Expected 1 query embedding, got {len(result.embeddings)}")
    return list(result.embeddings[0].values)


def as_vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def main() -> None:
    load_local_env()
    args = parse_args()
    query_embedding = embed_query(args.query, args.model, args.output_dimensionality)

    sql = """
        select
          tc.id,
          tc.page_start,
          tc.page_end,
          tc.chunk_type,
          tce.embedding_model,
          1 - (tce.embedding <=> %s::vector) as similarity,
          tc.clean_text
        from textbook_chunk_embeddings tce
        join textbook_chunks tc on tc.id = tce.chunk_id
    """

    params: list[object] = [as_vector_literal(query_embedding)]

    if args.embedding_model_filter:
        sql += " where tce.embedding_model = %s"
        params.append(args.embedding_model_filter)

    sql += """
        order by tce.embedding <=> %s::vector
        limit %s
    """
    params.append(as_vector_literal(query_embedding))
    params.append(args.top_k)

    with psycopg.connect(args.db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    if not rows:
        print("No matches found.")
        return

    for i, row in enumerate(rows, start=1):
        chunk_id, page_start, page_end, chunk_type, embedding_model, similarity, clean_text = row
        excerpt = clean_text[:400].replace("\n", " ")
        print(f"{i}. similarity={similarity:.4f} page={page_start}-{page_end} type={chunk_type}")
        print(f"   chunk_id={chunk_id}")
        print(f"   embedding_model={embedding_model}")
        print(f"   text={excerpt}")
        print()


if __name__ == "__main__":
    main()
