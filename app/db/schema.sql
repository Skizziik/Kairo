-- Kairo schema — run this once in Supabase SQL Editor.
-- Dimension 1024 matches Mistral `mistral-embed`. If you change EMBED_MODEL,
-- adjust the vector(1024) type and the EMBED_DIM env var together.

create extension if not exists vector;

create table if not exists users (
    tg_id       bigint primary key,
    username    text,
    first_name  text,
    last_name   text,
    is_admin    boolean not null default false,
    joined_at   timestamptz not null default now(),
    seen_at     timestamptz not null default now()
);

create table if not exists user_profiles (
    tg_id       bigint primary key references users(tg_id) on delete cascade,
    summary     text not null default '',
    traits      jsonb not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);

create table if not exists messages (
    id          bigserial primary key,
    chat_id     bigint not null,
    tg_user_id  bigint not null,
    reply_to    bigint,
    text        text not null,
    is_bot      boolean not null default false,
    created_at  timestamptz not null default now()
);
create index if not exists idx_messages_chat_created on messages (chat_id, created_at desc);
create index if not exists idx_messages_user on messages (tg_user_id, created_at desc);

create table if not exists memories (
    id          bigserial primary key,
    user_id     bigint not null references users(tg_id) on delete cascade,
    content     text not null,
    embedding   vector(1024) not null,
    importance  int not null default 1,
    created_at  timestamptz not null default now()
);
create index if not exists idx_memories_user on memories (user_id);
create index if not exists idx_memories_embedding on memories
    using ivfflat (embedding vector_cosine_ops) with (lists = 50);

create table if not exists warns (
    id          bigserial primary key,
    tg_user_id  bigint not null,
    chat_id     bigint not null,
    reason      text,
    issued_by   bigint not null,
    issued_at   timestamptz not null default now()
);

create table if not exists lfg_sessions (
    id          bigserial primary key,
    chat_id     bigint not null,
    initiator   bigint not null,
    participants jsonb not null default '[]'::jsonb,
    status      text not null default 'open',
    created_at  timestamptz not null default now(),
    closed_at   timestamptz
);

-- runtime counter for "extract memories every N messages"
create table if not exists kv_state (
    k text primary key,
    v bigint not null default 0
);
insert into kv_state (k, v) values ('msgs_since_extract', 0)
    on conflict (k) do nothing;

-- user-to-user relationships inside a chat (friends, teammates, beef)
create table if not exists relationships (
    chat_id    bigint not null,
    user_a     bigint not null,
    user_b     bigint not null,
    kind       text not null,        -- friends / teammates / rivals / beef / neutral
    note       text,                 -- short freeform e.g. "часто флудят 1v1 перестрелки"
    strength   int not null default 1,  -- 1..5
    updated_at timestamptz not null default now(),
    primary key (chat_id, user_a, user_b),
    check (user_a < user_b)         -- canonical ordering, one row per pair
);
create index if not exists idx_relationships_chat on relationships (chat_id);

-- track bot's own recent messages + feedback (emoji reactions) for self-learning
create table if not exists bot_messages (
    id           bigserial primary key,
    chat_id      bigint not null,
    message_id   bigint not null,
    text         text not null,
    created_at   timestamptz not null default now(),
    reaction     text,           -- first reaction received (emoji)
    reaction_by  bigint,         -- tg_id of reactor
    reaction_at  timestamptz,
    unique (chat_id, message_id)
);
create index if not exists idx_bot_messages_recent on bot_messages (chat_id, created_at desc);

-- per-chat persistent bot state (recent openers for anti-repetition,
-- chime cooldown timestamp, misc extras)
create table if not exists bot_chat_state (
    chat_id         bigint primary key,
    recent_openers  jsonb not null default '[]'::jsonb,
    last_chime_at   timestamptz,
    extras          jsonb not null default '{}'::jsonb,
    updated_at      timestamptz not null default now()
);
