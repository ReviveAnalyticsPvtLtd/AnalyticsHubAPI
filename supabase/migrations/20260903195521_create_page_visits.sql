create table public.page_visits (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null unique,
    path varchar(2048) not null check (char_length(path) between 1 and 2048),
    user_agent varchar(1024) check (char_length(user_agent) <= 1024),
    ip_address inet,
    created_at timestamptz not null default now()
);

create index page_visits_created_at_idx on public.page_visits (created_at);

alter table public.page_visits enable row level security;

revoke all on table public.page_visits from public;
revoke all on table public.page_visits from anon;
revoke all on table public.page_visits from authenticated;
revoke all on table public.page_visits from service_role;
grant select, insert on table public.page_visits to service_role;
