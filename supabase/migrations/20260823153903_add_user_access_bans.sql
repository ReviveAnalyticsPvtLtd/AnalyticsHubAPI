alter table public."Users"
    add column if not exists "isBanned" boolean not null default false,
    add column if not exists "bannedAt" timestamptz,
    add column if not exists "bannedBy" uuid,
    add column if not exists "banReason" text;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'users_banned_by_fkey'
          and conrelid = 'public."Users"'::regclass
    ) then
        alter table public."Users"
            add constraint users_banned_by_fkey
            foreign key ("bannedBy")
            references public.admin_users(id);
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'users_ban_reason_length_chk'
          and conrelid = 'public."Users"'::regclass
    ) then
        alter table public."Users"
            add constraint users_ban_reason_length_chk
            check (
                "banReason" is null
                or char_length("banReason") <= 1000
            );
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'users_ban_state_chk'
          and conrelid = 'public."Users"'::regclass
    ) then
        alter table public."Users"
            add constraint users_ban_state_chk
            check (
                (
                    "isBanned"
                    and "bannedAt" is not null
                    and "bannedBy" is not null
                )
                or (
                    not "isBanned"
                    and "bannedAt" is null
                    and "bannedBy" is null
                    and "banReason" is null
                )
            );
    end if;
end
$$;

alter table public.admin_audit_log
    add column if not exists details jsonb not null default '{}'::jsonb;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'admin_audit_log_details_chk'
          and conrelid = 'public.admin_audit_log'::regclass
    ) then
        alter table public.admin_audit_log
            add constraint admin_audit_log_details_chk
            check (jsonb_typeof(details) = 'object');
    end if;
end
$$;
