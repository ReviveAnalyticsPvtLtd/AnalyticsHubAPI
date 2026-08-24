alter table public.subscriptions
    add column if not exists admin_credit_generation bigint not null default 0;

comment on column public.subscriptions.admin_credit_generation is
    'Monotonic fence advanced only by administrator free-trial credit resets.';

create table public.admin_free_trial_extension_batches (
    id uuid primary key default gen_random_uuid(),
    idempotency_key uuid not null unique,
    request_hash text not null,
    days integer not null,
    reason text,
    requested_count integer not null,
    requested_by uuid not null references public.admin_users(id),
    status text not null default 'IN_PROGRESS',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    constraint admin_free_trial_extension_batches_hash_chk
        check (request_hash ~ '^[0-9a-f]{64}$'),
    constraint admin_free_trial_extension_batches_days_chk
        check (days between 1 and 30),
    constraint admin_free_trial_extension_batches_reason_length_chk
        check (reason is null or char_length(reason) <= 1000),
    constraint admin_free_trial_extension_batches_count_chk
        check (requested_count between 1 and 100),
    constraint admin_free_trial_extension_batches_status_chk
        check (status in ('IN_PROGRESS', 'COMPLETED')),
    constraint admin_free_trial_extension_batches_completed_chk
        check (status <> 'COMPLETED' or completed_at is not null)
);

comment on table public.admin_free_trial_extension_batches is
    'Idempotent administrator batch ledger for free-trial extensions.';
comment on column public.admin_free_trial_extension_batches.request_hash is
    'SHA-256 of the canonical request payload; contains no raw user identity.';

create index admin_free_trial_extension_batches_requested_by_idx
    on public.admin_free_trial_extension_batches (requested_by, created_at desc);

create table public.admin_free_trial_extension_items (
    id uuid primary key default gen_random_uuid(),
    batch_id uuid not null
        references public.admin_free_trial_extension_batches(id) on delete cascade,
    user_id text not null,
    subscription_id uuid,
    outcome text not null,
    days_added integer,
    previous_expiry timestamptz,
    new_expiry timestamptz,
    credit_sync_status text not null,
    credit_quota bigint,
    credit_topup_tokens bigint,
    credit_period_end timestamptz,
    credit_generation bigint,
    access_still_banned boolean not null default false,
    error_code text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint admin_free_trial_extension_items_batch_user_key
        unique (batch_id, user_id),
    constraint admin_free_trial_extension_items_outcome_chk
        check (outcome in ('EXTENDED', 'FAILED')),
    constraint admin_free_trial_extension_items_days_chk
        check (days_added is null or days_added between 1 and 30),
    constraint admin_free_trial_extension_items_credit_status_chk
        check (credit_sync_status in (
            'PENDING', 'SYNCED', 'SUPERSEDED', 'CANCELLED', 'NOT_APPLICABLE'
        )),
    constraint admin_free_trial_extension_items_credit_values_chk
        check (
            (credit_quota is null or credit_quota >= 0)
            and (credit_topup_tokens is null or credit_topup_tokens >= 0)
        ),
    constraint admin_free_trial_extension_items_shape_chk
        check (
            (
                outcome = 'EXTENDED'
                and days_added is not null
                and new_expiry is not null
                and credit_sync_status in (
                    'PENDING', 'SYNCED', 'SUPERSEDED', 'CANCELLED'
                )
                and credit_quota is not null
                and credit_topup_tokens is not null
                and credit_period_end is not null
                and credit_generation is not null
                and credit_generation >= 0
                and error_code is null
            )
            or (
                outcome = 'FAILED'
                and days_added is null
                and previous_expiry is null
                and new_expiry is null
                and credit_sync_status = 'NOT_APPLICABLE'
                and credit_quota is null
                and credit_topup_tokens is null
                and credit_period_end is null
                and credit_generation is null
                and error_code is not null
            )
        )
);

comment on table public.admin_free_trial_extension_items is
    'Per-user result and Redis credit-sync outbox for an admin trial-extension batch.';
comment on column public.admin_free_trial_extension_items.user_id is
    'Product user identifier. Explicitly erased by the user-erasure workflow; no foreign key permits durable USER_NOT_FOUND results.';

create index admin_free_trial_extension_items_user_idx
    on public.admin_free_trial_extension_items (user_id, created_at desc);

create index admin_free_trial_extension_items_pending_credit_idx
    on public.admin_free_trial_extension_items (created_at)
    where credit_sync_status = 'PENDING';

alter table public.admin_free_trial_extension_batches enable row level security;
alter table public.admin_free_trial_extension_items enable row level security;

revoke all on table public.admin_free_trial_extension_batches
    from anon, authenticated;
revoke all on table public.admin_free_trial_extension_items
    from anon, authenticated;

grant select, insert, update
    on table public.admin_free_trial_extension_batches to service_role;
grant select, insert, update, delete
    on table public.admin_free_trial_extension_items to service_role;

create or replace function public.reconcile_credit_balance_if_no_admin_refresh(
    p_user_id text,
    p_remaining_tokens bigint,
    p_used_tokens bigint,
    p_last_reconciled_at timestamptz,
    p_period_start timestamptz,
    p_period_end timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
    perform pg_advisory_xact_lock(hashtextextended(p_user_id, 0));

    update public.credit_balances as balance
    set remaining_tokens = greatest(0, p_remaining_tokens),
        used_tokens = greatest(0, p_used_tokens),
        period_start = coalesce(p_period_start, balance.period_start),
        period_end = coalesce(p_period_end, balance.period_end),
        last_reset_at = case
            when p_period_end is null then balance.last_reset_at
            else p_last_reconciled_at
        end,
        last_reconciled_at = p_last_reconciled_at,
        updated_at = p_last_reconciled_at
    where balance.user_id = p_user_id
      and not exists (
          select 1
          from public.admin_free_trial_extension_items as item
          where item.user_id = p_user_id
            and item.credit_sync_status = 'PENDING'
      );

    return found;
end;
$$;

revoke all on function public.reconcile_credit_balance_if_no_admin_refresh(
    text, bigint, bigint, timestamptz, timestamptz, timestamptz
) from public, anon, authenticated;
grant execute on function public.reconcile_credit_balance_if_no_admin_refresh(
    text, bigint, bigint, timestamptz, timestamptz, timestamptz
) to service_role;
