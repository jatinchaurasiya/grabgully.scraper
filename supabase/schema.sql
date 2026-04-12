-- ═══════════════════════════════════════════════════════════════════════════
--  GRAB GULLY — Supabase PostgreSQL Schema
--  Run this in: Supabase Dashboard → SQL Editor → New Query → Run
--  Order matters — run top to bottom once on a fresh project.
-- ═══════════════════════════════════════════════════════════════════════════

-- Enable UUID extension (already enabled on Supabase, but safe to run again)
create extension if not exists "uuid-ossp";
create extension if not exists "pg_trgm";    -- For fuzzy title search


-- ─── platform_listings ────────────────────────────────────────────────────────
-- One row per (platform, external_id) pair.
-- This is the core table — everything joins to it.
create table if not exists platform_listings (
    id              uuid primary key default uuid_generate_v4(),
    platform        text not null,           -- amazon | flipkart | myntra | meesho | ajio | snapdeal
    external_id     text not null,           -- Platform's own product ID or ASIN
    title           text not null,
    brand           text not null default '',
    image_url       text not null default '',
    current_price   numeric(10, 2) not null check (current_price >= 0),
    original_price  numeric(10, 2) not null default 0 check (original_price >= 0),
    discount_pct    integer not null default 0 check (discount_pct between 0 and 100),
    affiliate_url   text not null,
    category        text not null default '',
    in_stock        boolean not null default true,
    rating          numeric(3,1) default 0,
    rating_count    integer default 0,
    updated_at      timestamptz not null default now(),
    created_at      timestamptz not null default now(),

    -- Unique constraint — upsert target
    constraint platform_listings_platform_external_id_key unique (platform, external_id)
);

-- Indexes for common query patterns
create index if not exists idx_listings_platform    on platform_listings (platform);
create index if not exists idx_listings_category    on platform_listings (category);
create index if not exists idx_listings_discount    on platform_listings (discount_pct desc);
create index if not exists idx_listings_updated     on platform_listings (updated_at desc);
create index if not exists idx_listings_in_stock    on platform_listings (in_stock) where in_stock = true;
-- Full-text search index on title
create index if not exists idx_listings_title_trgm  on platform_listings using gin (title gin_trgm_ops);


-- ─── price_history ────────────────────────────────────────────────────────────
-- Append-only time series. One row per price observation.
-- Used for Vico charts in CompareScreen.
create table if not exists price_history (
    id          bigserial primary key,
    listing_id  uuid not null references platform_listings (id) on delete cascade,
    price       numeric(10, 2) not null,
    scraped_at  timestamptz not null default now()
);

create index if not exists idx_price_history_listing on price_history (listing_id, scraped_at desc);
create index if not exists idx_price_history_date    on price_history (scraped_at desc);


-- ─── users ────────────────────────────────────────────────────────────────────
-- Extends Supabase auth.users — do NOT replace auth.users, just add profile data.
create table if not exists users (
    id            uuid primary key references auth.users (id) on delete cascade,
    username      text unique,
    avatar_url    text default '',
    xp            integer not null default 0 check (xp >= 0),
    level         integer not null default 1 check (level >= 1),
    fcm_token     text,                      -- Firebase Cloud Messaging device token
    referral_code text unique default upper(substr(md5(random()::text), 1, 8)),
    referred_by   uuid references users (id),
    is_pro        boolean not null default false,
    pro_expires_at timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists idx_users_xp           on users (xp desc);
create index if not exists idx_users_referral_code on users (referral_code);


-- ─── watchlist ────────────────────────────────────────────────────────────────
create table if not exists watchlist (
    id            uuid primary key default uuid_generate_v4(),
    user_id       uuid not null references users (id) on delete cascade,
    listing_id    uuid not null references platform_listings (id) on delete cascade,
    target_price  numeric(10, 2),            -- NULL = watch for any drop
    is_notified   boolean not null default false,
    notified_at   timestamptz,
    created_at    timestamptz not null default now(),

    constraint watchlist_user_listing_unique unique (user_id, listing_id)
);

create index if not exists idx_watchlist_user        on watchlist (user_id);
create index if not exists idx_watchlist_unnotified  on watchlist (is_notified) where is_notified = false;


-- ─── xp_events ────────────────────────────────────────────────────────────────
-- Audit log of every XP-earning action.
create table if not exists xp_events (
    id           bigserial primary key,
    user_id      uuid not null references users (id) on delete cascade,
    action_type  text not null,    -- daily_login | deal_view | deal_share | affiliate_click | watchlist_add | referral
    xp_amount    integer not null check (xp_amount > 0),
    metadata     jsonb default '{}',
    created_at   timestamptz not null default now()
);

create index if not exists idx_xp_events_user on xp_events (user_id, created_at desc);


-- ─── achievements ─────────────────────────────────────────────────────────────
create table if not exists achievements (
    id          uuid primary key default uuid_generate_v4(),
    slug        text unique not null,
    name        text not null,
    description text not null,
    badge_url   text default '',
    xp_reward   integer not null default 0,
    condition   jsonb not null default '{}'  -- e.g. {"action": "affiliate_click", "count": 10}
);

-- Seed achievement definitions
insert into achievements (slug, name, description, xp_reward, condition) values
    ('first_deal_view',    'Tota Saver',          'Pehla deal dekha!',                   50,  '{"action": "deal_view", "count": 1}'),
    ('first_affiliate',    'Pehli Khareedi',       'Pehli baar affiliate link se khareeda', 200, '{"action": "affiliate_click", "count": 1}'),
    ('streak_7',           '7 Din Ka Banda',       '7 din lagatar aaya!',                 100, '{"action": "daily_login", "streak": 7}'),
    ('streak_30',          'Mahine Ka Veer',       '30 din non-stop!',                    500, '{"action": "daily_login", "streak": 30}'),
    ('share_10',           'Sab Ko Bataya',        '10 deals share kiye',                 150, '{"action": "deal_share", "count": 10}'),
    ('watchlist_10',       'Nazar Wala',           'Watchlist mein 10 items daale',       100, '{"action": "watchlist_add", "count": 10}'),
    ('saved_1000',         'Rs 1000 Bachaya',      'Total Rs 1000 ki savings!',           200, '{"savings": 1000}'),
    ('saved_10000',        'Rs 10000 Bachaya',     'Total Rs 10000 ki savings!',          1000,'{"savings": 10000}'),
    ('referral_1',         'Dost Banaya',          'Ek dost ko invite kiya',              200, '{"action": "referral", "count": 1}'),
    ('referral_5',         'Gully Ambassador',     '5 doston ko invite kiya',             1000,'{"action": "referral", "count": 5}')
on conflict (slug) do nothing;


-- ─── user_achievements ────────────────────────────────────────────────────────
create table if not exists user_achievements (
    id             uuid primary key default uuid_generate_v4(),
    user_id        uuid not null references users (id) on delete cascade,
    achievement_id uuid not null references achievements (id),
    unlocked_at    timestamptz not null default now(),
    constraint user_achievements_unique unique (user_id, achievement_id)
);


-- ─── affiliate_clicks ─────────────────────────────────────────────────────────
-- Audit trail for every affiliate redirect. Used for commission disputes.
create table if not exists affiliate_clicks (
    id          bigserial primary key,
    user_id     uuid references users (id) on delete set null,
    listing_id  uuid not null references platform_listings (id) on delete cascade,
    platform    text not null,
    ip_hash     text not null,             -- SHA-256 of IP, not raw IP (privacy)
    clicked_at  timestamptz not null default now()
);

create index if not exists idx_aff_clicks_listing on affiliate_clicks (listing_id, clicked_at desc);
create index if not exists idx_aff_clicks_user    on affiliate_clicks (user_id, clicked_at desc);


-- ─── referrals ────────────────────────────────────────────────────────────────
create table if not exists referrals (
    id               uuid primary key default uuid_generate_v4(),
    referrer_id      uuid not null references users (id) on delete cascade,
    referee_id       uuid not null references users (id) on delete cascade,
    xp_bonus_paid    boolean not null default false,
    converted_at     timestamptz not null default now(),
    constraint referrals_unique unique (referrer_id, referee_id)
);


-- ─── scraper_runs ────────────────────────────────────────────────────────────
-- Operational log for monitoring scraper health in Railway.
create table if not exists scraper_runs (
    id               bigserial primary key,
    platform         text not null,
    category         text not null default 'all',
    products_found   integer not null default 0,
    duration_seconds numeric(8, 2) not null default 0,
    status           text not null,          -- success | failed
    error            text,
    ran_at           timestamptz not null default now()
);

create index if not exists idx_scraper_runs_ran_at on scraper_runs (ran_at desc);


-- ═══════════════════════════════════════════════════════════════════════════
--  ROW LEVEL SECURITY (RLS)
--  Critical: without RLS, any Supabase anon key can read/write all data.
-- ═══════════════════════════════════════════════════════════════════════════

-- Enable RLS on all user-data tables
alter table users             enable row level security;
alter table watchlist         enable row level security;
alter table xp_events         enable row level security;
alter table user_achievements enable row level security;
alter table affiliate_clicks  enable row level security;
alter table referrals         enable row level security;

-- Public read tables (deal data — no PII)
alter table platform_listings enable row level security;
alter table price_history     enable row level security;
alter table achievements      enable row level security;

-- ── Public read policies ──────────────────────────────────────────────────────
create policy "Anyone can read listings"
    on platform_listings for select using (true);

create policy "Anyone can read price_history"
    on price_history for select using (true);

create policy "Anyone can read achievements"
    on achievements for select using (true);

-- ── User data policies — users can only see their own data ────────────────────
create policy "Users read own profile"
    on users for select using (auth.uid() = id);

create policy "Users update own profile"
    on users for update using (auth.uid() = id);

create policy "Users read own watchlist"
    on watchlist for select using (auth.uid() = user_id);

create policy "Users insert own watchlist"
    on watchlist for insert with check (auth.uid() = user_id);

create policy "Users update own watchlist"
    on watchlist for update using (auth.uid() = user_id);

create policy "Users delete own watchlist"
    on watchlist for delete using (auth.uid() = user_id);

create policy "Users read own xp_events"
    on xp_events for select using (auth.uid() = user_id);

create policy "Users read own achievements"
    on user_achievements for select using (auth.uid() = user_id);

-- ── Service role bypass (for our Python backend) ─────────────────────────────
-- The SUPABASE_SERVICE_KEY bypasses RLS automatically — this is correct.
-- NEVER use the service key in the Android app. Only in the Railway backend.


-- ═══════════════════════════════════════════════════════════════════════════
--  FUNCTIONS & TRIGGERS
-- ═══════════════════════════════════════════════════════════════════════════

-- Auto-create user profile on signup
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
    insert into public.users (id, username, avatar_url)
    values (
        new.id,
        split_part(new.email, '@', 1),
        new.raw_user_meta_data ->> 'avatar_url'
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();


-- Atomic XP increment (called from Python backend via supabase.rpc())
create or replace function public.increment_user_xp(p_user_id uuid, p_xp integer)
returns void language plpgsql security definer as $$
declare
    new_xp    integer;
    new_level integer;
begin
    update users
    set xp = xp + p_xp, updated_at = now()
    where id = p_user_id
    returning xp into new_xp;

    -- Recalculate level based on XP thresholds
    new_level := case
        when new_xp >= 50000 then 6
        when new_xp >= 15000 then 5
        when new_xp >= 5000  then 4
        when new_xp >= 2000  then 3
        when new_xp >= 500   then 2
        else 1
    end;

    update users set level = new_level where id = p_user_id;
end;
$$;


-- Auto-update updated_at on platform_listings
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists set_listings_updated_at on platform_listings;
create trigger set_listings_updated_at
    before update on platform_listings
    for each row execute function public.set_updated_at();
