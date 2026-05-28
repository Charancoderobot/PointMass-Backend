# Physics Practice Site Architecture

## Goal

Build an Alcumus-like physics practice website that:

- organizes practice by textbook topic and subtopic
- supports difficulty selection
- stores textbook-derived embeddings for retrieval and tagging
- tracks user progress over time
- supports normal username/password login

This document assumes the source content starts from `college-physics-ap-courses-2e_-_WEB.pdf`.

## Recommended Stack

Use a simple, durable stack first:

- Frontend: `Next.js` with TypeScript
- Backend: `Next.js API routes` or a separate `FastAPI` service
- Primary database: `PostgreSQL`
- Vector storage: `pgvector` in the same PostgreSQL instance
- Blob/file storage: local `data/` during development, `S3-compatible` storage in production
- Background jobs: simple queued ingestion worker

Why this is the right starting point:

- one database handles auth, progress, topics, and embeddings
- `pgvector` is good enough for textbook-scale retrieval
- you avoid premature complexity from adding Pinecone/Weaviate/Qdrant too early

## What To Store

Store four different content layers separately.

### 1. Canonical textbook structure

This is the clean hierarchy you browse in the UI:

- book
- chapter
- section
- subsection
- concept/topic tag

### 2. Text chunks

Chunk the textbook into retrieval units such as:

- concept explanation chunks
- worked example chunks
- formula definition chunks
- end-of-section summary chunks

Each chunk should keep:

- `chunk_id`
- `book_id`
- `chapter_number`
- `section_number`
- `title`
- `chunk_type`
- `raw_text`
- `clean_text`
- `page_start`
- `page_end`
- `token_count`

### 3. Problems

Problems should be stored separately from textbook chunks, even if they are derived from the book.

Each problem should keep:

- `problem_id`
- `source_type` (`textbook`, `generated`, `teacher_authored`)
- `prompt`
- `answer_type` (`numeric`, `multiple_choice`, `free_response`, `symbolic`)
- `canonical_answer`
- `solution_text`
- `difficulty`
- `chapter_id`
- `section_id`
- `topic_id`
- `skills_tested`

### 4. Embeddings

Do not embed only one thing. Store embeddings for:

- textbook chunks
- problem statements
- worked solutions
- topic summaries

This lets you do:

- topic search
- “find related problems”
- “retrieve context before generating hints”
- clustering by concept/difficulty later

## Best Initial Embeddings Strategy

Start with `PostgreSQL + pgvector` and a separate metadata table.

Do not start by putting only vectors in a vector database with weak metadata. You need relational filtering immediately for:

- chapter/section browsing
- difficulty filtering
- “only AP mechanics” style constraints
- progress-aware question selection

### Suggested embedding tables

```sql
textbook_chunks (
  id uuid primary key,
  section_id uuid not null,
  chunk_type text not null,
  title text,
  raw_text text not null,
  clean_text text not null,
  page_start int,
  page_end int,
  token_count int,
  created_at timestamptz default now()
);

textbook_chunk_embeddings (
  chunk_id uuid primary key references textbook_chunks(id) on delete cascade,
  embedding_model text not null,
  embedding vector(1536) not null,
  created_at timestamptz default now()
);

problems (
  id uuid primary key,
  section_id uuid,
  topic_id uuid,
  prompt text not null,
  answer_type text not null,
  canonical_answer jsonb,
  solution_text text,
  difficulty smallint not null,
  source_type text not null,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

problem_embeddings (
  problem_id uuid primary key references problems(id) on delete cascade,
  embedding_model text not null,
  embedding vector(1536) not null,
  created_at timestamptz default now()
);
```

### Why separate the vectors from the main rows

- easier to re-embed when models change
- easier to keep multiple embedding versions later
- cleaner migrations

If you want model versioning, switch the embedding primary key to `(chunk_id, embedding_model)`.

## Raw File Layout

Use this project structure:

```text
PointMass/
  app/
  components/
  lib/
  backend/
  docs/
  data/
    raw/
      textbook/
        college-physics-ap-courses-2e_-_WEB.pdf
    extracted/
      textbook_pages.jsonl
      textbook_sections.jsonl
      textbook_chunks.jsonl
      textbook_problems.jsonl
    embeddings/
      textbook_chunks_v1.jsonl
      problems_v1.jsonl
  scripts/
    ingest_textbook.py
    chunk_textbook.py
    classify_topics.py
    embed_content.py
  prisma/ or migrations/
```

### What lives in files vs database

Keep these in files:

- original PDF
- extraction artifacts
- JSONL exports from ingestion
- optional backup copies of embeddings

Keep these in PostgreSQL:

- users
- topics/sections/problems
- vectors
- attempts
- mastery state
- spaced repetition / next-question signals

## Topic And Difficulty Model

Do not rely on embeddings alone for topic labels or difficulty.

Use a hybrid model:

- textbook hierarchy gives the primary topic tree
- embeddings help with retrieval and relatedness
- explicit tags define the actual assignment logic
- difficulty starts as rule-based, then becomes empirical

### Topic hierarchy

Start with:

- `Unit`
- `Chapter`
- `Section`
- `Topic`
- `Skill`

Example:

- Mechanics
- Kinematics
- Motion in One Dimension
- Constant-Acceleration Equations
- Solve for final velocity

### Difficulty

Use three difficulty sources:

1. `author_difficulty`
2. `text_complexity_signals`
3. `observed_student_difficulty`

Initial difficulty can be based on:

- number of steps
- algebraic manipulation burden
- number of formulas involved
- presence of diagrams
- whether unit conversion is required

Later, update difficulty from attempt data:

- median accuracy
- median solve time
- hint usage
- retry count

## Backend Logic

Recommended backend modules:

- `auth`
- `content_ingestion`
- `topic_catalog`
- `problem_selection`
- `attempts`
- `mastery`
- `retrieval`

### Core backend flows

#### 1. Textbook ingestion

- parse PDF into page text
- detect chapter and section headings
- extract end-of-section problems if available
- create normalized section/topic records
- chunk explanatory content
- embed chunks and problems

#### 2. Practice session generation

Given user filters:

- topic
- difficulty
- due-review items
- recent incorrect topics

Select candidate questions by:

- matching explicit topic tags first
- filtering by difficulty band
- excluding very recent repeats
- boosting weak-skill areas

Embeddings should support:

- pulling related textbook context
- finding substitute questions when the exact pool is small

#### 3. Hint generation

Do not generate hints from the whole textbook blindly.

Instead:

- retrieve the top relevant chunk embeddings for the problem
- pass only those chunks to the hint/explanation system
- store the retrieved chunk ids with the hint event

This keeps hints grounded.

## Frontend Layout

Recommended student-facing layout:

### Main navigation

- Dashboard
- Practice
- Topics
- Progress
- Review

### Practice page

Left sidebar:

- topic tree
- difficulty selector
- question count target

Main panel:

- current problem
- answer input
- hint button
- relevant formula/reference card

Right rail:

- progress in current set
- streak
- recent weak skills

### Dashboard

Show:

- mastery by topic
- recent activity
- recommended next practice set
- accuracy trend
- time spent

### Topic page

Show:

- chapter and section hierarchy
- per-topic mastery
- available problem counts by difficulty

## Username/Password Storage

Use standard database auth storage. Never store plaintext passwords.

Recommended user table:

```sql
users (
  id uuid primary key,
  username text unique not null,
  email text unique,
  password_hash text not null,
  created_at timestamptz default now(),
  last_login_at timestamptz
);
```

Use:

- `argon2id` for password hashing
- session cookies or JWTs
- email optional at first if you want low-friction signup

If you use Next.js, `next-auth` or a simple custom credentials flow is fine.

## Progress Tracking

You need both event history and summary state.

### Event tables

```sql
problem_attempts (
  id uuid primary key,
  user_id uuid not null references users(id),
  problem_id uuid not null references problems(id),
  submitted_answer jsonb,
  is_correct boolean not null,
  seconds_spent int,
  hints_used int default 0,
  attempt_number int not null,
  created_at timestamptz default now()
);

hint_events (
  id uuid primary key,
  user_id uuid not null references users(id),
  problem_id uuid not null references problems(id),
  retrieved_chunk_ids jsonb,
  created_at timestamptz default now()
);
```

### Summary tables

```sql
user_topic_mastery (
  user_id uuid not null references users(id),
  topic_id uuid not null,
  mastery_score real not null default 0,
  last_practiced_at timestamptz,
  problems_seen int not null default 0,
  problems_correct int not null default 0,
  primary key (user_id, topic_id)
);
```

Keep every attempt forever, and derive summary state from it.

## Recommended Retrieval Strategy

Use embeddings for retrieval, not as your primary source of truth.

The retrieval stack should be:

1. explicit metadata filter
2. vector similarity
3. optional rerank

Example:

- filter to `topic = kinematics`
- search similar chunks/problems by vector
- rerank by exact section match + difficulty proximity

This is much better than global nearest-neighbor search over the entire book.

## Good First Version

For version 1, build only:

- textbook section/topic ingestion
- tagged problem bank
- username/password login
- practice by topic
- 1 to 5 difficulty scale
- attempt logging
- basic mastery score
- `pgvector` retrieval for hints and related content

Do not build initially:

- full LLM-generated tutoring
- adaptive Bayesian mastery models
- a separate vector database
- multi-book support

## Concrete Recommendation

If you want the most practical starting point, do this:

1. Put the PDF under `data/raw/textbook/`.
2. Extract pages and sections into JSONL files under `data/extracted/`.
3. Store normalized textbook structure, problems, users, attempts, and topic mastery in PostgreSQL.
4. Store chunk/problem embeddings in `pgvector` tables inside that same PostgreSQL database.
5. Use metadata-based topic filtering first, vector search second.
6. Derive difficulty from explicit tags first, then refine it from actual student performance.

## Next Build Order

1. Create the PostgreSQL schema with `pgvector`.
2. Write the ingestion script for the OpenStax PDF.
3. Extract chapter/section/topic structure.
4. Create chunk records and embeddings.
5. Build the practice UI around explicit topic selection.
6. Add progress tracking and mastery updates.
7. Add retrieval-backed hints.

This gives you a system that is easy to reason about, easy to query, and strong enough to grow into a more adaptive platform later.
