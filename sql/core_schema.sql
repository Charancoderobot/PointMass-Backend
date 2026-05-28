-- Core schema for PointMass v1
-- Requires: PostgreSQL 14+ with pgvector extension available.

create extension if not exists vector;

create table if not exists books (
  id uuid primary key,
  title text not null,
  slug text unique not null,
  created_at timestamptz not null default now()
);

create table if not exists sections (
  id uuid primary key,
  book_id uuid not null references books(id) on delete cascade,
  chapter_number int not null,
  section_number text not null,
  title text not null,
  created_at timestamptz not null default now(),
  unique (book_id, chapter_number, section_number)
);

create table if not exists topics (
  id uuid primary key,
  section_id uuid references sections(id) on delete set null,
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists textbook_chunks (
  id uuid primary key,
  section_id uuid references sections(id) on delete set null,
  chunk_type text not null default 'explanation',
  title text,
  raw_text text not null,
  clean_text text not null,
  page_start int,
  page_end int,
  token_count int,
  created_at timestamptz not null default now()
);

create table if not exists textbook_chunk_embeddings (
  chunk_id uuid not null references textbook_chunks(id) on delete cascade,
  embedding_model text not null,
  embedding vector(1536) not null,
  created_at timestamptz not null default now(),
  primary key (chunk_id, embedding_model)
);

create table if not exists problems (
  id uuid primary key,
  section_id uuid references sections(id) on delete set null,
  topic_id uuid references topics(id) on delete set null,
  prompt text not null,
  answer_type text not null,
  canonical_answer jsonb,
  solution_text text,
  difficulty smallint not null check (difficulty between 1 and 5),
  source_type text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists problem_embeddings (
  problem_id uuid not null references problems(id) on delete cascade,
  embedding_model text not null,
  embedding vector(1536) not null,
  created_at timestamptz not null default now(),
  primary key (problem_id, embedding_model)
);

create table if not exists users (
  id uuid primary key,
  username text unique not null,
  email text unique,
  password_hash text not null,
  created_at timestamptz not null default now(),
  last_login_at timestamptz
);

create table if not exists problem_attempts (
  id uuid primary key,
  user_id uuid not null references users(id) on delete cascade,
  problem_id uuid not null references problems(id) on delete cascade,
  submitted_answer jsonb,
  is_correct boolean not null,
  seconds_spent int,
  hints_used int not null default 0,
  attempt_number int not null,
  created_at timestamptz not null default now()
);

create table if not exists hint_events (
  id uuid primary key,
  user_id uuid not null references users(id) on delete cascade,
  problem_id uuid not null references problems(id) on delete cascade,
  retrieved_chunk_ids jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists user_topic_mastery (
  user_id uuid not null references users(id) on delete cascade,
  topic_id uuid not null references topics(id) on delete cascade,
  mastery_score real not null default 0,
  last_practiced_at timestamptz,
  problems_seen int not null default 0,
  problems_correct int not null default 0,
  primary key (user_id, topic_id)
);

create index if not exists idx_sections_book_chapter
  on sections (book_id, chapter_number, section_number);

create index if not exists idx_chunks_section
  on textbook_chunks (section_id);

create index if not exists idx_problems_section_topic_difficulty
  on problems (section_id, topic_id, difficulty);

create index if not exists idx_attempts_user_created
  on problem_attempts (user_id, created_at desc);

create index if not exists idx_tce_embedding_hnsw
  on textbook_chunk_embeddings using hnsw (embedding vector_cosine_ops);

create index if not exists idx_pe_embedding_hnsw
  on problem_embeddings using hnsw (embedding vector_cosine_ops);
