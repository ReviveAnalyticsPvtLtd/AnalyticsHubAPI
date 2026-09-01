create table public.admin_free_trial_extensions (
    id uuid primary key default gen_random_uuid(),
    idempotency_key uuid not null unique,
    request_hash text not null,
    user_id text not null,
    subscription_id uuid,
    requested_by uuid not null references public.admin_users(id),
    days integer not null,
    reason text,
    outcome text not null default 'PENDING',
    days_added integer,
    previous_expiry timestamptz,
    new_expiry timestamptz,
    credit_sync_status text not null default 'NOT_APPLICABLE',
    credit_quota bigint,
    credit_topup_tokens bigint,
    credit_period_end timestamptz,
    credit_generation bigint,
    access_still_banned boolean not null default false,
    error_code text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    constraint admin_free_trial_extensions_hash_chk
        check (request_hash ~ '^[0-9a-f]{64}$'),
    constraint admin_free_trial_extensions_days_chk
        check (days between 1 and 30),
    constraint admin_free_trial_extensions_reason_length_chk
        check (reason is null or char_length(reason) <= 1000),
    constraint admin_free_trial_extensions_outcome_chk
        check (outcome in ('PENDING', 'EXTENDED', 'FAILED')),
    constraint admin_free_trial_extensions_days_added_chk
        check (days_added is null or days_added between 1 and 30),
    constraint admin_free_trial_extensions_credit_status_chk
        check (credit_sync_status in (
            'PENDING', 'SYNCED', 'SUPERSEDED', 'CANCELLED', 'NOT_APPLICABLE'
        )),
    constraint admin_free_trial_extensions_credit_values_chk
        check (
            (credit_quota is null or credit_quota >= 0)
            and (credit_topup_tokens is null or credit_topup_tokens >= 0)
            and (credit_generation is null or credit_generation >= 0)
        ),
    constraint admin_free_trial_extensions_shape_chk
        check (
            (
                outcome = 'PENDING'
                and completed_at is null
                and days_added is null
                and previous_expiry is null
                and new_expiry is null
                and credit_sync_status = 'NOT_APPLICABLE'
                and credit_quota is null
                and credit_topup_tokens is null
                and credit_period_end is null
                and credit_generation is null
                and error_code is null
            )
            or (
                outcome = 'EXTENDED'
                and completed_at is not null
                and days_added is not null
                and new_expiry is not null
                and credit_sync_status in (
                    'PENDING', 'SYNCED', 'SUPERSEDED', 'CANCELLED'
                )
                and credit_quota is not null
                and credit_topup_tokens is not null
                and credit_period_end is not null
                and credit_generation is not null
                and error_code is null
            )
            or (
                outcome = 'FAILED'
                and completed_at is not null
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

comment on table public.admin_free_trial_extensions is
    'Idempotent single-user administrator free-trial extension ledger and credit-sync outbox.';
comment on column public.admin_free_trial_extensions.user_id is
    'Product user identifier. The user-erasure workflow removes matching operations.';

create index admin_free_trial_extensions_requested_by_idx
    on public.admin_free_trial_extensions (requested_by, created_at desc);

create index admin_free_trial_extensions_user_idx
    on public.admin_free_trial_extensions (user_id, created_at desc);

create index admin_free_trial_extensions_pending_credit_idx
    on public.admin_free_trial_extensions (created_at)
    where credit_sync_status = 'PENDING';

alter table public.admin_free_trial_extensions enable row level security;

revoke all on table public.admin_free_trial_extensions
    from anon, authenticated;

grant select, insert, update, delete
    on table public.admin_free_trial_extensions to service_role;

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
          from public.admin_free_trial_extensions as extension
          where extension.user_id = p_user_id
            and extension.credit_sync_status = 'PENDING'
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

drop table if exists public.admin_free_trial_extension_items;
drop table if exists public.admin_free_trial_extension_batches;
