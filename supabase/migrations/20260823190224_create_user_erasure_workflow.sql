create table public.user_erasure_requests (
    id uuid primary key default gen_random_uuid(),
    target_user_id text,
    subject_fingerprint text not null,
    requested_by uuid not null references public.admin_users(id),
    idempotency_key uuid not null unique,
    status text not null default 'PENDING',
    reason text,
    resource_manifest jsonb not null default '{}'::jsonb,
    retention_manifest jsonb not null default '{}'::jsonb,
    last_error_code text,
    attempt_count integer not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    next_retry_at timestamptz,
    lease_expires_at timestamptz,
    worker_id text,
    constraint user_erasure_requests_fingerprint_chk
        check (subject_fingerprint ~ '^[0-9a-f]{64}$'),
    constraint user_erasure_requests_status_chk
        check (status in (
            'PENDING', 'IN_PROGRESS', 'PARTIALLY_FAILED', 'COMPLETED'
        )),
    constraint user_erasure_requests_reason_length_chk
        check (reason is null or char_length(reason) <= 1000),
    constraint user_erasure_requests_resource_manifest_chk
        check (jsonb_typeof(resource_manifest) = 'object'),
    constraint user_erasure_requests_retention_manifest_chk
        check (jsonb_typeof(retention_manifest) = 'object'),
    constraint user_erasure_requests_attempt_count_chk
        check (attempt_count >= 0),
    constraint user_erasure_requests_completed_chk
        check (
            status <> 'COMPLETED'
            or (
                completed_at is not null
                and target_user_id is null
                and reason is null
                and lease_expires_at is null
                and worker_id is null
            )
        )
);

comment on table public.user_erasure_requests is
    'Durable administrator-initiated user-erasure ledger. Raw target identity and reason are scrubbed after verified completion.';
comment on column public.user_erasure_requests.target_user_id is
    'Temporary product user identifier retained only while erasure is active; intentionally has no Users foreign key so retries remain possible after product-row deletion.';
comment on column public.user_erasure_requests.subject_fingerprint is
    'HMAC-SHA256 fingerprint used for non-reversible correlation after target identity is scrubbed.';
comment on column public.user_erasure_requests.resource_manifest is
    'Temporary project/workspace ownership identifiers and safe counts used for crash recovery; scrubbed at completion and never contains object paths, emails, prompts, tokens, or provider payloads.';
comment on column public.user_erasure_requests.retention_manifest is
    'Safe retained-resource categories and policy codes only; must never contain direct identifiers.';

create unique index user_erasure_requests_active_user_uidx
    on public.user_erasure_requests (target_user_id)
    where target_user_id is not null and status <> 'COMPLETED';

create index user_erasure_requests_requested_by_idx
    on public.user_erasure_requests (requested_by);

create index user_erasure_requests_retry_idx
    on public.user_erasure_requests (status, next_retry_at, created_at)
    where status in ('PENDING', 'PARTIALLY_FAILED', 'IN_PROGRESS');

create table public.user_erasure_steps (
    id uuid primary key default gen_random_uuid(),
    request_id uuid not null
        references public.user_erasure_requests(id) on delete cascade,
    step_name text not null,
    status text not null default 'PENDING',
    attempt_count integer not null default 0,
    last_error_code text,
    started_at timestamptz,
    completed_at timestamptz,
    last_error_at timestamptz,
    next_retry_at timestamptz,
    details jsonb not null default '{}'::jsonb,
    constraint user_erasure_steps_name_chk
        check (step_name in (
            'revoke_access',
            'inventory',
            'stop_billing',
            'delete_storage',
            'delete_transient_state',
            'delete_auth_identity',
            'delete_database_data',
            'verify_and_finalize'
        )),
    constraint user_erasure_steps_status_chk
        check (status in (
            'PENDING', 'IN_PROGRESS', 'COMPLETED',
            'FAILED', 'SKIPPED', 'RETAINED'
        )),
    constraint user_erasure_steps_attempt_count_chk
        check (attempt_count >= 0),
    constraint user_erasure_steps_details_chk
        check (jsonb_typeof(details) = 'object'),
    constraint user_erasure_steps_request_name_key
        unique (request_id, step_name)
);

comment on table public.user_erasure_steps is
    'Idempotent per-system steps for a durable user-erasure request.';
comment on column public.user_erasure_steps.details is
    'Safe counts and stable provider result codes only; never raw errors or user content.';

create index user_erasure_steps_request_status_idx
    on public.user_erasure_steps (request_id, status);

create index user_erasure_steps_retry_idx
    on public.user_erasure_steps (next_retry_at)
    where status = 'FAILED';

alter table public.user_erasure_requests enable row level security;
alter table public.user_erasure_steps enable row level security;

revoke all on table public.user_erasure_requests from anon, authenticated;
revoke all on table public.user_erasure_steps from anon, authenticated;

grant select, insert, update
    on table public.user_erasure_requests to service_role;
grant select, insert, update
    on table public.user_erasure_steps to service_role;

alter table public.subscriptions
    add column if not exists erasure_pending boolean not null default false;

alter table if exists public."Invoices"
    alter column "userId" drop not null,
    alter column subscription_id drop not null;

alter table if exists public.billing_events
    alter column user_id drop not null,
    alter column subscription_id drop not null;

comment on column public.subscriptions.erasure_pending is
    'Fail-closed billing and mutation interlock while a user-erasure workflow is active.';

create index if not exists subscriptions_erasure_pending_idx
    on public.subscriptions (user_id)
    where erasure_pending;

alter table public."WebhookEvents"
    add column if not exists user_id text;

update public."WebhookEvents" as webhook
set user_id = coalesce(
    webhook.payload #>> '{payload,payment,entity,notes,userId}',
    webhook.payload #>> '{payload,payment,entity,notes,user_id}',
    webhook.payload #>> '{payload,order,entity,notes,userId}',
    webhook.payload #>> '{payload,order,entity,notes,user_id}',
    webhook.payload #>> '{payload,invoice,entity,notes,userId}',
    webhook.payload #>> '{payload,invoice,entity,notes,user_id}',
    webhook.payload #>> '{payload,token,entity,notes,userId}',
    webhook.payload #>> '{payload,token,entity,notes,user_id}'
)
where webhook.user_id is null
  and exists (
      select 1
      from public."Users" as subject
      where subject."userId" = coalesce(
          webhook.payload #>> '{payload,payment,entity,notes,userId}',
          webhook.payload #>> '{payload,payment,entity,notes,user_id}',
          webhook.payload #>> '{payload,order,entity,notes,userId}',
          webhook.payload #>> '{payload,order,entity,notes,user_id}',
          webhook.payload #>> '{payload,invoice,entity,notes,userId}',
          webhook.payload #>> '{payload,invoice,entity,notes,user_id}',
          webhook.payload #>> '{payload,token,entity,notes,userId}',
          webhook.payload #>> '{payload,token,entity,notes,user_id}'
      )
  );

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'webhook_events_user_id_fkey'
          and conrelid = 'public."WebhookEvents"'::regclass
    ) then
        alter table public."WebhookEvents"
            add constraint webhook_events_user_id_fkey
            foreign key (user_id)
            references public."Users"("userId")
            on delete set null;
    end if;
end
$$;

create index if not exists webhook_events_user_id_idx
    on public."WebhookEvents" (user_id)
    where user_id is not null;

alter table if exists public.message_store
    add column if not exists user_id text,
    add column if not exists project_id text;

do $$
begin
    if to_regclass('public.message_store') is not null then
        if not exists (
            select 1
            from pg_constraint
            where conname = 'message_store_user_id_fkey'
              and conrelid = 'public.message_store'::regclass
        ) then
            alter table public.message_store
                add constraint message_store_user_id_fkey
                foreign key (user_id)
                references public."Users"("userId")
                on delete cascade;
        end if;

        if not exists (
            select 1
            from pg_constraint
            where conname = 'message_store_project_id_fkey'
              and conrelid = 'public.message_store'::regclass
        ) then
            alter table public.message_store
                add constraint message_store_project_id_fkey
                foreign key (project_id)
                references public."Projects"("projectId")
                on delete cascade;
        end if;

        create index if not exists message_store_user_id_idx
            on public.message_store (user_id)
            where user_id is not null;
        create index if not exists message_store_project_id_idx
            on public.message_store (project_id)
            where project_id is not null;
    end if;
end
$$;

comment on column public."WebhookEvents".user_id is
    'Deterministic product-user linkage used to sanitize retained webhook history during erasure.';

grant update (target_id, details)
    on table public.admin_audit_log to service_role;
