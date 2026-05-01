-- Migration: persistent per-chat bot state.
-- Run once in Supabase SQL Editor. Safe to re-run (idempotent).

create table if not exists bot_chat_state (
    chat_id         bigint primary key,
    recent_openers  jsonb not null default '[]'::jsonb,  -- last ~10 opener words
    last_chime_at   timestamptz,
    extras          jsonb not null default '{}'::jsonb,  -- future use
    updated_at      timestamptz not null default now()
);

-- Mood/persona state — динамическая личность (Кайро 2.0)
-- mood/energy/offended/toxicity, last_persona, day_seed. Полная схема в mood_engine.py.
alter table bot_chat_state
    add column if not exists mood_state jsonb not null default '{}'::jsonb;
