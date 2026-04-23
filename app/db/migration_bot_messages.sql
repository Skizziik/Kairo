-- Migration: bot self-learning feedback.
create table if not exists bot_messages (
    id           bigserial primary key,
    chat_id      bigint not null,
    message_id   bigint not null,
    text         text not null,
    created_at   timestamptz not null default now(),
    reaction     text,
    reaction_by  bigint,
    reaction_at  timestamptz,
    unique (chat_id, message_id)
);
create index if not exists idx_bot_messages_recent on bot_messages (chat_id, created_at desc);
