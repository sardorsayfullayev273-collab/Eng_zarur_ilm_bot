CREATE TABLE IF NOT EXISTS users (
  id BIGINT PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  last_name TEXT,
  audience TEXT NOT NULL DEFAULT 'both',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  streak INTEGER NOT NULL DEFAULT 0,
  points INTEGER NOT NULL DEFAULT 0,
  last_learned DATE,
  notifications BOOLEAN NOT NULL DEFAULT TRUE,
  last_daily_sent DATE,
  ref_code TEXT UNIQUE,
  referred_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS learned (
  user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
  hadith_id INTEGER NOT NULL,
  learned_on DATE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id, hadith_id)
);

CREATE TABLE IF NOT EXISTS referrals (
  referrer_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
  invited_id BIGINT UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_events (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT,
  event TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
