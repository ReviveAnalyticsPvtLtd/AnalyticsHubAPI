create index if not exists user_erasure_requests_subject_fingerprint_idx
    on public.user_erasure_requests (subject_fingerprint);

create table public.user_erasure_batches (
    id uuid primary key default gen_random_uuid(),
    requested_by uuid not null references public.admin_users(id),
    idempotency_key uuid not null unique,
    request_hash text not null,
    status text not null default 'PREVIEWED',
    reason text,
    requested_count integer not null,
    ready_count integer not null,
    expires_at timestamptz not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    confirmed_at timestamptz,
    completed_at timestamptz,
    constraint user_erasure_batches_status_chk
        check (status in (
            'PREVIEWED', 'IN_PROGRESS', 'PARTIALLY_FAILED',
            'COMPLETED', 'EXPIRED'
        )),
    constraint user_erasure_batches_request_hash_chk
        check (request_hash ~ '^[0-9a-f]{64}$'),
    constraint user_erasure_batches_reason_length_chk
        check (reason is null or char_length(reason) <= 1000),
    constraint user_erasure_batches_counts_chk
        check (
            requested_count between 1 and 25
            and ready_count between 0 and requested_count
        ),
    constraint user_erasure_batches_expiry_chk
        check (expires_at > created_at),
    constraint user_erasure_batches_timestamps_chk
        check (
            (
                status = 'PREVIEWED'
                and confirmed_at is null
                and completed_at is null
            )
            or (
                status in ('IN_PROGRESS', 'PARTIALLY_FAILED')
                and confirmed_at is not null
                and completed_at is null
            )
            or (
                status = 'COMPLETED'
                and confirmed_at is not null
                and completed_at is not null
            )
            or (
                status = 'EXPIRED'
                and confirmed_at is null
                and completed_at is not null
            )
        )
);

comment on table public.user_erasure_batches is
    'Private durable ledger for reviewed administrator user-erasure batches.';
comment on column public.user_erasure_batches.request_hash is
    'SHA-256 of the canonical normalized preview request; contains no raw subject identity.';
comment on column public.user_erasure_batches.reason is
    'Temporary internal reason scrubbed when the preview expires or the batch completes.';

create index user_erasure_batches_requested_by_idx
    on public.user_erasure_batches (requested_by, created_at desc);

create index user_erasure_batches_preview_expiry_idx
    on public.user_erasure_batches (expires_at)
    where status = 'PREVIEWED';

create index user_erasure_batches_active_status_idx
    on public.user_erasure_batches (status, updated_at)
    where status in ('IN_PROGRESS', 'PARTIALLY_FAILED');

create table public.user_erasure_batch_items (
    id uuid primary key default gen_random_uuid(),
    batch_id uuid not null
        references public.user_erasure_batches(id) on delete cascade,
    ordinal integer not null,
    target_user_id text,
    subject_fingerprint text not null,
    classification text not null,
    request_id uuid references public.user_erasure_requests(id),
    error_code text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint user_erasure_batch_items_ordinal_chk
        check (ordinal between 0 and 24),
    constraint user_erasure_batch_items_fingerprint_chk
        check (subject_fingerprint ~ '^[0-9a-f]{64}$'),
    constraint user_erasure_batch_items_classification_chk
        check (classification in (
            'READY', 'ALREADY_IN_PROGRESS',
            'ALREADY_COMPLETED', 'USER_NOT_FOUND'
        )),
    constraint user_erasure_batch_items_batch_ordinal_key
        unique (batch_id, ordinal),
    constraint user_erasure_batch_items_batch_fingerprint_key
        unique (batch_id, subject_fingerprint)
);

comment on table public.user_erasure_batch_items is
    'Private ordered preview classifications linked to authoritative user-erasure requests.';
comment on column public.user_erasure_batch_items.target_user_id is
    'Temporary product user identifier scrubbed after completion or preview expiry; intentionally has no Users foreign key.';
comment on column public.user_erasure_batch_items.subject_fingerprint is
    'HMAC-SHA256 subject fingerprint used for non-reversible completed-erasure correlation.';

create index user_erasure_batch_items_request_idx
    on public.user_erasure_batch_items (request_id)
    where request_id is not null;

create index user_erasure_batch_items_active_classification_idx
    on public.user_erasure_batch_items (batch_id, classification, ordinal)
    where classification in ('READY', 'ALREADY_IN_PROGRESS');

alter table public.user_erasure_batches enable row level security;
alter table public.user_erasure_batch_items enable row level security;

revoke all on table public.user_erasure_batches
    from public, anon, authenticated;
revoke all on table public.user_erasure_batch_items
    from public, anon, authenticated;

revoke all on table public.user_erasure_batches from service_role;
revoke all on table public.user_erasure_batch_items from service_role;

grant select, insert, update
    on table public.user_erasure_batches to service_role;
grant select, insert, update
    on table public.user_erasure_batch_items to service_role;
