create table public.admin_audit_log (
    id uuid primary key default gen_random_uuid(),
    admin_id uuid,
    admin_email text not null,
    session_id uuid,
    actor_type text not null,
    action text not null,
    target_type text not null,
    target_id text,
    changed_fields jsonb not null default '[]'::jsonb,
    outcome text not null,
    created_at timestamptz not null default now(),
    constraint admin_audit_log_actor_type_chk
        check (actor_type in ('admin', 'cli')),
    constraint admin_audit_log_admin_email_chk
        check (btrim(admin_email) <> ''),
    constraint admin_audit_log_changed_fields_chk
        check (jsonb_typeof(changed_fields) = 'array'),
    constraint admin_audit_log_admin_actor_chk
        check (
            actor_type <> 'admin'
            or (admin_id is not null and session_id is not null)
        )
);

create index admin_audit_log_created_at_idx
    on public.admin_audit_log (created_at desc);

create index admin_audit_log_target_idx
    on public.admin_audit_log (target_type, target_id);

alter table public.admin_audit_log enable row level security;

revoke all on table public.admin_audit_log from anon, authenticated;

-- no update grant: an audit row that can be edited is not an audit row.
-- delete is granted solely for the retention sweep in adminSessionCleanupTask.
grant select, insert, delete on table public.admin_audit_log to service_role;

-- the cleanup task prunes expired sessions; 20260813112853_create_admin_auth.sql
-- granted only select, insert, and update on this table.
grant delete on table public.admin_sessions to service_role;
