-- Chat-Unterhaltungen + Artefakte (idempotent für laufende Dev-DBs)
CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title      VARCHAR(255) NOT NULL DEFAULT 'Neue Unterhaltung',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             VARCHAR(32) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content          TEXT NOT NULL,
    cad_session_id   VARCHAR(64),
    meta             JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_artifacts (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    message_id       UUID REFERENCES conversation_messages(id) ON DELETE SET NULL,
    cad_session_id   VARCHAR(64),
    kind             VARCHAR(32) NOT NULL CHECK (kind IN ('concept_image', 'step', 'stl', 'transcript')),
    file_path        VARCHAR(1024) NOT NULL,
    label            VARCHAR(255),
    part_index       INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_conv ON conversation_messages (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_artifacts_conv ON conversation_artifacts (conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations (updated_at DESC);
