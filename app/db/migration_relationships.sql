-- Migration: user-to-user relationships in chat.
create table if not exists relationships (
    chat_id    bigint not null,
    user_a     bigint not null,
    user_b     bigint not null,
    kind       text not null,
    note       text,
    strength   int not null default 1,
    updated_at timestamptz not null default now(),
    primary key (chat_id, user_a, user_b),
    check (user_a < user_b)
);
create index if not exists idx_relationships_chat on relationships (chat_id);
