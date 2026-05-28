#!/usr/bin/env python3
"""
Load textbook chunk embeddings JSONL into PostgreSQL.

Expected JSONL row format:
{
  "id": "...uuid...",
  "page": 12,
  "chunk_index_on_page": 1,
  "text": "...",
  "embedding_model": "text-embedding-3-small",
  "embedding": [ ... 1536 floats ... ]
}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg

from env_utils import load_local_env


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Load textbook chunk embeddings into PostgreSQL.")
    p.add_argument("--db-url", required=True, help="PostgreSQL URL, e.g. postgresql://user:pass@localhost:5432/pointmass")
    p.add_argument("--jsonl", required=True, help="Path to embeddings JSONL file")
    p.add_argument("--chunk-type", default="explanation", help="Default chunk_type value for inserted chunks")
    return p.parse_args()


def as_vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def main() -> None:
    load_local_env()
    args = parse_args()
    jsonl_path = Path(args.jsonl).resolve()
    if not jsonl_path.exists():
        raise FileNotFoundError(f"JSONL file not found: {jsonl_path}")

    inserted_chunks = 0
    inserted_embeddings = 0

    with psycopg.connect(args.db_url) as conn:
        with conn.cursor() as cur, jsonl_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                row = json.loads(line)

                chunk_id = row["id"]
                page = row.get("page")
                text = row["text"]
                embedding_model = row["embedding_model"]
                embedding = row["embedding"]

                if len(embedding) != 1536:
                    raise ValueError(
                        f"Line {line_num}: expected embedding length 1536, got {len(embedding)}"
                    )

                cur.execute(
                    """
                    insert into textbook_chunks (
                      id, section_id, chunk_type, title, raw_text, clean_text, page_start, page_end, token_count
                    )
                    values (%s, null, %s, null, %s, %s, %s, %s, %s)
                    on conflict (id) do update set
                      raw_text = excluded.raw_text,
                      clean_text = excluded.clean_text,
                      page_start = excluded.page_start,
                      page_end = excluded.page_end,
                      token_count = excluded.token_count
                    """,
                    (
                        chunk_id,
                        args.chunk_type,
                        text,
                        text,
                        page,
                        page,
                        len(text.split()),
                    ),
                )
                inserted_chunks += 1

                cur.execute(
                    """
                    insert into textbook_chunk_embeddings (chunk_id, embedding_model, embedding)
                    values (%s, %s, %s::vector)
                    on conflict (chunk_id, embedding_model) do update set
                      embedding = excluded.embedding
                    """,
                    (
                        chunk_id,
                        embedding_model,
                        as_vector_literal(embedding),
                    ),
                )
                inserted_embeddings += 1

        conn.commit()

    print(f"Loaded chunks: {inserted_chunks}")
    print(f"Loaded embeddings: {inserted_embeddings}")


if __name__ == "__main__":
    main()
