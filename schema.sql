CREATE TABLE IF NOT EXISTS vk_posts (
  id BIGSERIAL PRIMARY KEY,
  vk_owner_id BIGINT NOT NULL,
  vk_post_id BIGINT NOT NULL,
  post_date TIMESTAMPTZ,
  text TEXT NOT NULL DEFAULT '',
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (vk_owner_id, vk_post_id)
);

CREATE TABLE IF NOT EXISTS images (
  id BIGSERIAL PRIMARY KEY,
  vk_photo_owner_id BIGINT NOT NULL,
  vk_photo_id BIGINT NOT NULL,
  access_key TEXT,
  width INTEGER,
  height INTEGER,
  source_url TEXT,
  size_bytes BIGINT,
  sha256 TEXT,
  upload_status TEXT NOT NULL DEFAULT 'pending',
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (vk_photo_owner_id, vk_photo_id)
);

CREATE TABLE IF NOT EXISTS post_images (
  post_id BIGINT NOT NULL REFERENCES vk_posts(id) ON DELETE CASCADE,
  image_id BIGINT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (post_id, image_id)
);

CREATE TABLE IF NOT EXISTS telegram_files (
  image_id BIGINT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
  chat_id TEXT NOT NULL,
  message_id BIGINT NOT NULL,
  file_id TEXT NOT NULL,
  file_unique_id TEXT,
  file_name TEXT,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS import_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vk_posts_date ON vk_posts(post_date DESC);
CREATE INDEX IF NOT EXISTS idx_post_images_image_id ON post_images(image_id);
