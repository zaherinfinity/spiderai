-- AI Translate Video — Supabase schema (2026)
-- Run this in Supabase SQL Editor if tables do not exist

create extension if not exists "uuid-ossp";

-- Users (password_hash for web accounts; admin uses separate login + API key)
create table if not exists public.users (
  id uuid primary key default uuid_generate_v4(),
  telegram_id text unique,
  email text unique,
  username text unique,
  full_name text,
  password_hash text,
  avatar_url text,
  role text default 'user' check (role in ('user', 'admin', 'moderator')),
  is_banned boolean default false,
  videos_count integer default 0,
  last_seen timestamptz default now(),
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Migration helpers if table already exists:
-- alter table public.users add column if not exists password_hash text;
-- create unique index if not exists users_username_uq on public.users (username);

-- Jobs / processing queue
create table if not exists public.jobs (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid references public.users(id) on delete set null,
  original_filename text,
  status text default 'pending' check (status in ('pending','downloading','transcribing','translating','tts','mixing','uploading','completed','failed')),
  progress integer default 0,
  language_detected text,
  original_text text,
  translated_text text,
  input_path text,
  output_path text,
  error_message text,
  file_size_mb numeric(10,2),
  duration_sec numeric(10,2),
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  completed_at timestamptz
);

-- Stats
create table if not exists public.stats (
  id integer primary key default 1,
  total_videos integer default 0,
  total_users integer default 0,
  total_failed integer default 0,
  started_at timestamptz default now(),
  updated_at timestamptz default now()
);

insert into public.stats (id, total_videos, total_users, total_failed)
values (1, 0, 0, 0)
on conflict (id) do nothing;

-- Banned list (quick lookup)
create table if not exists public.banned (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid references public.users(id) on delete cascade,
  reason text,
  banned_by uuid,
  created_at timestamptz default now()
);

-- Admin activity log
create table if not exists public.admin_logs (
  id uuid primary key default uuid_generate_v4(),
  admin_id uuid,
  action text,
  target_id text,
  meta jsonb,
  created_at timestamptz default now()
);

-- Settings
create table if not exists public.settings (
  key text primary key,
  value jsonb,
  updated_at timestamptz default now()
);

insert into public.settings (key, value) values
  ('max_file_size_mb', '20'),
  ('whisper_model', '"base"'),
  ('khmer_voice', '"km-KH-SreymomNeural"'),
  ('bg_volume', '0.20'),
  ('remove_vocals', 'true'),
  ('supportor', '"@foundcount1"'),
  ('site_name', '"AI Translate Video"'),
  ('powered_by', '"SPIDER ZAHER"')
on conflict (key) do nothing;

-- Indexes
create index if not exists idx_jobs_user on public.jobs(user_id);
create index if not exists idx_jobs_status on public.jobs(status);
create index if not exists idx_users_role on public.users(role);
create index if not exists idx_users_banned on public.users(is_banned);

-- RLS (optional – service role bypasses)
alter table public.users enable row level security;
alter table public.jobs enable row level security;
alter table public.stats enable row level security;
alter table public.banned enable row level security;
alter table public.admin_logs enable row level security;
alter table public.settings enable row level security;

-- Service role policies can be added later; for now service key is used server-side.

-- Notifications (admin → all users)
create table if not exists public.notifications (
  id uuid primary key default uuid_generate_v4(),
  title text not null,
  body text not null,
  type text default 'update' check (type in ('update','alert','breaking','info')),
  is_active boolean default true,
  created_by text,
  created_at timestamptz default now(),
  expires_at timestamptz
);

create table if not exists public.notification_reads (
  id uuid primary key default uuid_generate_v4(),
  notification_id uuid references public.notifications(id) on delete cascade,
  user_id uuid references public.users(id) on delete cascade,
  read_at timestamptz default now(),
  unique(notification_id, user_id)
);

create index if not exists idx_notifications_created on public.notifications(created_at desc);
create index if not exists idx_notif_reads_user on public.notification_reads(user_id);
