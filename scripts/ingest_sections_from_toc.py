#!/usr/bin/env python3
"""
Parse textbook sections from the embedded table of contents rows and backfill
textbook_chunks.section_id by page range.
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg

from env_utils import load_local_env


BOOK_TITLE = "College Physics for AP Courses 2e"
BOOK_SLUG = "college-physics-ap-courses-2e"
BOOK_NAMESPACE = uuid.UUID("8d97aaf0-734a-4da6-a767-03ca4d3d78a8")


@dataclass(frozen=True)
class TocSection:
    chapter_number: int
    chapter_title: str
    section_number: str
    section_title: str
    book_page_start: int
    book_page_end: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest textbook sections from TOC text and backfill chunk section_ids.")
    p.add_argument("--db-url", required=True, help="PostgreSQL URL")
    p.add_argument("--jsonl", required=True, help="Path to full embeddings JSONL file")
    p.add_argument(
        "--toc-page-max",
        type=int,
        default=18,
        help="Highest PDF page to scan for contents rows",
    )
    p.add_argument(
        "--pdf-page-offset",
        type=int,
        default=18,
        help="Offset to convert textbook page numbers from the TOC into PDF page numbers",
    )
    return p.parse_args()


def normalize_toc_text(text: str) -> str:
    text = text.replace("CHAP TER", "CHAPTER")
    text = re.sub(r"Access for free at openstax\.org", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load_toc_text(jsonl_path: Path, toc_page_max: int) -> str:
    parts: list[tuple[int, int, str]] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            page = row["page"]
            if page > toc_page_max:
                continue
            text = row["text"]
            if "CHAP TER" not in text and "CHAPTER" not in text and not re.search(r"\b1\.1\b", text):
                continue
            parts.append((page, row["chunk_index_on_page"], text))

    if not parts:
        raise RuntimeError("Could not find TOC text in the JSONL file.")

    parts.sort()
    return normalize_toc_text(" ".join(text for _, _, text in parts))


def parse_toc_sections(toc_text: str) -> list[TocSection]:
    chapter_pattern = re.compile(
        r"CHAPTER\s+(\d+)\s+(.+?)\s+(\d+)(?=\s+(?:Connection for AP|CHAPTER\s+\d+|\d+\.\d+|Appendix|Answer Key|Index|$))"
    )
    section_pattern = re.compile(
        r"(\d+\.\d+)\s+(.+?)\s+(\d+)(?=\s+(?:\d+\.\d+|Glossary|Section Summary|Conceptual Questions|Problems\s*&\s*Exercises|Test Prep for AP|CHAPTER\s+\d+|Appendix|Answer Key|Index|$))"
    )

    chapters: dict[int, str] = {}
    for match in chapter_pattern.finditer(toc_text):
        chapter_number = int(match.group(1))
        chapter_title = match.group(2).strip(" -")
        chapters[chapter_number] = chapter_title

    parsed: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    for match in section_pattern.finditer(toc_text):
        section_number = match.group(1)
        if section_number in seen:
            continue
        seen.add(section_number)
        parsed.append((section_number, match.group(2).strip(" -"), int(match.group(3))))

    if not parsed:
        raise RuntimeError("Could not parse any section entries from the TOC text.")

    sections: list[TocSection] = []
    for index, (section_number, section_title, page_start) in enumerate(parsed):
        chapter_number = int(section_number.split(".", 1)[0])
        chapter_title = chapters.get(chapter_number)
        if not chapter_title:
            raise RuntimeError(f"Missing chapter title for section {section_number}")
        next_page_start = parsed[index + 1][2] if index + 1 < len(parsed) else 1773
        sections.append(
            TocSection(
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                section_number=section_number,
                section_title=section_title,
                book_page_start=page_start,
                book_page_end=next_page_start - 1,
            )
        )

    return sections


def book_id() -> uuid.UUID:
    return uuid.uuid5(BOOK_NAMESPACE, f"book:{BOOK_SLUG}")


def section_id(section_number: str) -> uuid.UUID:
    return uuid.uuid5(BOOK_NAMESPACE, f"section:{BOOK_SLUG}:{section_number}")


def upsert_book_and_sections(db_url: str, sections: list[TocSection], pdf_page_offset: int) -> tuple[int, int]:
    inserted_sections = 0
    updated_chunks = 0

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            bid = book_id()
            cur.execute(
                """
                insert into books (id, title, slug)
                values (%s, %s, %s)
                on conflict (id) do update set
                  title = excluded.title,
                  slug = excluded.slug
                """,
                (bid, BOOK_TITLE, BOOK_SLUG),
            )

            for section in sections:
                sid = section_id(section.section_number)
                cur.execute(
                    """
                    insert into sections (id, book_id, chapter_number, section_number, title)
                    values (%s, %s, %s, %s, %s)
                    on conflict (id) do update set
                      chapter_number = excluded.chapter_number,
                      section_number = excluded.section_number,
                      title = excluded.title
                    """,
                    (
                        sid,
                        bid,
                        section.chapter_number,
                        section.section_number,
                        section.section_title,
                    ),
                )
                inserted_sections += 1

            # Replace prior heuristic assignments so reruns are deterministic.
            cur.execute("update textbook_chunks set section_id = null")

            for section in sections:
                sid = section_id(section.section_number)
                pdf_page_start = section.book_page_start + pdf_page_offset
                pdf_page_end = section.book_page_end + pdf_page_offset
                cur.execute(
                    """
                    update textbook_chunks
                    set section_id = %s
                    where page_start >= %s and page_start <= %s
                    """,
                    (sid, pdf_page_start, pdf_page_end),
                )
                updated_chunks += cur.rowcount

        conn.commit()

    return inserted_sections, updated_chunks


def main() -> None:
    load_local_env()
    args = parse_args()
    jsonl_path = Path(args.jsonl).resolve()
    if not jsonl_path.exists():
        raise FileNotFoundError(f"JSONL file not found: {jsonl_path}")

    toc_text = load_toc_text(jsonl_path, args.toc_page_max)
    sections = parse_toc_sections(toc_text)
    inserted_sections, updated_chunks = upsert_book_and_sections(
        args.db_url,
        sections,
        pdf_page_offset=args.pdf_page_offset,
    )

    print(f"Parsed sections: {len(sections)}")
    print(f"Upserted sections: {inserted_sections}")
    print(f"Updated chunk rows: {updated_chunks}")


if __name__ == "__main__":
    main()
