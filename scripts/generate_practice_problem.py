#!/usr/bin/env python3
"""
Generate a grounded physics practice problem from retrieved textbook chunks.
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
    p = argparse.ArgumentParser(description="Generate a practice problem from textbook context.")
    p.add_argument("topic", nargs="?", help="Topic or concept for the practice problem")
    p.add_argument("--difficulty", type=int, default=3, choices=[1, 2, 3, 4, 5], help="Difficulty from 1-5")
    p.add_argument(
        "--answer-type",
        default="numeric",
        choices=["numeric", "multiple_choice", "free_response"],
        help="Type of answer to produce",
    )
    p.add_argument("--top-k", type=int, default=TOP_K, help="How many chunks to retrieve")
    p.add_argument(
        "--quiz",
        action="store_true",
        help="Show only the problem first, then reveal answer/solution after Enter",
    )
    return p.parse_args()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set.")
    return value


def embed_query(topic: str) -> list[float]:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.")

    client = genai.Client(api_key=api_key)
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=topic,
        config=types.EmbedContentConfig(output_dimensionality=OUTPUT_DIMENSIONALITY),
    )
    if len(result.embeddings) != 1:
        raise RuntimeError(f"Expected 1 query embedding, got {len(result.embeddings)}")
    return list(result.embeddings[0].values)


def as_vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def retrieve_chunks(db_url: str, topic_embedding: list[float], top_k: int) -> list[tuple]:
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
    query_vector = as_vector_literal(topic_embedding)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (query_vector, EMBEDDING_MODEL, query_vector, top_k))
            return cur.fetchall()


def build_prompt(topic: str, difficulty: int, answer_type: str, chunks: list[tuple]) -> str:
    context_parts: list[str] = []
    for index, (_, page_start, page_end, text, similarity) in enumerate(chunks, start=1):
        context_parts.append(
            f"Source {index} | pages {page_start}-{page_end} | similarity {similarity:.4f}\n{text}"
        )
    context = "\n\n".join(context_parts)

    return f"""You are creating one physics practice problem grounded ONLY in the retrieved textbook excerpts.

Target topic:
{topic}

Difficulty:
{difficulty} on a 1-5 scale (1 easiest, 5 hardest)

Answer type:
{answer_type}

Retrieved excerpts:
{context}

Output format (use exactly these headings):
Problem:
<the question text>

Answer:
<final answer only>

Solution:
<step-by-step solution>

Rules:
- Use only the retrieved excerpts; do not add outside facts.
- If the context is insufficient, write a simpler problem that is still supported by the excerpts.
- Keep numbers realistic and solvable by hand.
- If answer type is multiple_choice, include exactly 4 options (A-D) inside Problem and provide only the correct letter+choice in Answer.
- If answer type is numeric, include units in both Problem and Answer when applicable.
- Keep the full output concise and classroom-ready.
"""


def generate_problem(prompt: str) -> str:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty practice problem.")
    return text


def split_sections(output_text: str) -> tuple[str, str, str]:
    labels = ["Problem:", "Answer:", "Solution:"]
    positions: dict[str, int] = {}
    for label in labels:
        idx = output_text.find(label)
        if idx == -1:
            raise RuntimeError(f"Model output missing required section: {label}")
        positions[label] = idx

    if not (positions["Problem:"] < positions["Answer:"] < positions["Solution:"]):
        raise RuntimeError("Model output sections are out of order.")

    problem = output_text[positions["Problem:"] : positions["Answer:"]].strip()
    answer = output_text[positions["Answer:"] : positions["Solution:"]].strip()
    solution = output_text[positions["Solution:"] :].strip()
    return problem, answer, solution


def main() -> None:
    load_local_env()
    args = parse_args()
    topic = args.topic
    if not topic:
        topic = input("Topic: ").strip()
    if not topic:
        raise RuntimeError("No topic provided.")

    db_url = require_env("DATABASE_URL")
    topic_embedding = embed_query(topic)
    chunks = retrieve_chunks(db_url, topic_embedding, args.top_k)
    if not chunks:
        raise RuntimeError("No matching chunks found in the database.")

    prompt = build_prompt(topic, args.difficulty, args.answer_type, chunks)
    generated = generate_problem(prompt)

    if not args.quiz:
        print(generated)
        return

    problem, answer, solution = split_sections(generated)
    print(problem)
    print()
    input("Press Enter to reveal answer and solution...")
    print()
    print(answer)
    print()
    print(solution)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
