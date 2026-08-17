create extension if not exists pgcrypto;

create table public.admin_users (
    id uuid primary key default gen_random_uuid(),
    email text not null,
    name text not null,
    password_hash text not null,
    is_active boolean not null default true,
    last_login_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint admin_users_email_normalized_chk
        check (email = lower(btrim(email)) and email <> ''),
    constraint admin_users_name_nonempty_chk
        check (btrim(name) <> ''),
    constraint admin_users_password_hash_nonempty_chk
        check (password_hash <> '')
);

create unique index admin_users_email_lower_uidx
    on public.admin_users (lower(email));

create table public.admin_sessions (
    id uuid primary key,
    admin_id uuid not null references public.admin_users(id) on delete cascade,
    token_hash text not null unique,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null,
    revoked_at timestamptz,
    last_used_at timestamptz not null default now(),
    constraint admin_sessions_token_hash_chk
        check (token_hash ~ '^[0-9a-f]{64}$'),
    constraint admin_sessions_expiry_chk
        check (expires_at > created_at)
);

create index admin_sessions_admin_id_idx
    on public.admin_sessions (admin_id);

create index admin_sessions_active_expiry_idx
    on public.admin_sessions (expires_at)
    where revoked_at is null;

alter table public.admin_users enable row level security;
alter table public.admin_sessions enable row level security;

revoke all on table public.admin_users from anon, authenticated;
revoke all on table public.admin_sessions from anon, authenticated;

grant select, insert, update on table public.admin_users to service_role;
grant select, insert, update on table public.admin_sessions to service_role;
