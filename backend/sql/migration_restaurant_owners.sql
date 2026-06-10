-- Owner login credentials (per restaurant, stored hashed — not in .env)

CREATE TABLE IF NOT EXISTS restaurant_owners (
  owner_id text PRIMARY KEY,
  restaurant_id text NOT NULL REFERENCES restaurants(restaurant_id) ON DELETE CASCADE,
  username text NOT NULL,
  password_hash text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS restaurant_owners_username_lower_idx
  ON restaurant_owners (lower(username));

CREATE UNIQUE INDEX IF NOT EXISTS restaurant_owners_restaurant_id_idx
  ON restaurant_owners (restaurant_id);

COMMENT ON TABLE restaurant_owners IS
  'Restaurant owner login: one active account per restaurant; username globally unique.';
