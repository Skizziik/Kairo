-- Migration: 384-dim (fastembed) -> 1024-dim (Mistral mistral-embed)
-- Run once in Supabase SQL Editor if you already ran the original schema.sql.
-- Drops any existing embeddings (table is empty anyway on first deploys).

drop index if exists idx_memories_embedding;
alter table memories alter column embedding type vector(1024) using null;
create index if not exists idx_memories_embedding on memories
    using ivfflat (embedding vector_cosine_ops) with (lists = 50);
