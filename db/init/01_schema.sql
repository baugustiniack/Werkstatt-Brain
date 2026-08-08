-- Werkstatt-Brain – Relationales Schema (SPEC Kap. 2.1.1)

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Werkzeugdatenbank
CREATE TABLE tools (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name             VARCHAR(255) NOT NULL,
    diameter_mm      NUMERIC(6, 2) NOT NULL,
    flute_length_mm  NUMERIC(6, 2),
    max_rpm          INTEGER,
    feed_rate_mm_min NUMERIC(8, 2),
    status           VARCHAR(50) NOT NULL DEFAULT 'neu'
                     CHECK (status IN ('neu', 'verschlissen', 'abgebrochen')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE tools IS 'Werkzeugdatenbank: Fräser-Inventar';
COMMENT ON COLUMN tools.status IS 'neu | verschlissen | abgebrochen';

-- Materiallager
CREATE TABLE stock_materials (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_type     VARCHAR(255) NOT NULL,
    dimensions_xyz_mm JSONB NOT NULL,
    grain_direction   VARCHAR(50),
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE stock_materials IS 'Materiallager: Vollformate und Restplatten';
COMMENT ON COLUMN stock_materials.dimensions_xyz_mm IS 'Abmessungen als JSON, z.B. {"x": 1220, "y": 610, "z": 18}';

-- Projekthistorie & CAD-Artefakte
CREATE TABLE projects_cad (
    id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_name           VARCHAR(255) NOT NULL,
    raw_prompt             TEXT,
    requirements_contract  JSONB,
    generated_code         TEXT,
    step_file_path         VARCHAR(512),
    stl_file_path          VARCHAR(512),
    gcode_file_path        VARCHAR(512),
    human_rating           SMALLINT CHECK (human_rating BETWEEN 1 AND 5),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE projects_cad IS 'Projekthistorie mit build123d-Code und Export-Pfaden';

-- Unstrukturierte Asset-Bibliothek: Dateien (Crawler/Upload) & manuelle
-- Einträge, KI-strukturiert auf Wunsch/im Hintergrund (SPEC Kap. 2.1.1, 2.3.1)
CREATE TABLE unprocessed_assets (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_path     VARCHAR(512) UNIQUE,
    file_hash     VARCHAR(64),
    file_type     VARCHAR(50) NOT NULL
                  CHECK (file_type IN ('image', 'step', 'stl', 'f3d', 'pdf', 'manual', 'other')),
    status        VARCHAR(50) NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'processing', 'indexed', 'failed')),
    source        VARCHAR(20) NOT NULL DEFAULT 'crawler'
                  CHECK (source IN ('crawler', 'upload', 'manual')),
    title         VARCHAR(255),
    notes         TEXT,
    tags          JSONB NOT NULL DEFAULT '[]',
    vision_result JSONB,
    error_message TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at  TIMESTAMPTZ
);

COMMENT ON TABLE unprocessed_assets IS 'Unstrukturierte Asset-Bibliothek: Dateien (Crawler/Upload) & manuelle Einträge';
COMMENT ON COLUMN unprocessed_assets.file_hash IS 'SHA256 zur Duplikaterkennung (NULL bei manuellen Einträgen ohne Datei)';

CREATE UNIQUE INDEX idx_unprocessed_assets_hash   ON unprocessed_assets (file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX        idx_unprocessed_assets_status ON unprocessed_assets (status) WHERE status = 'pending';

-- UI-verwaltete Laufzeit-Einstellungen (z. B. API-Keys statt nur .env)
CREATE TABLE app_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE app_settings IS 'UI-verwaltete Laufzeit-Einstellungen (Key-Value), z.B. Anthropic-API-Key';

-- Log-Archiv für den Meta-Coach (SPEC Kap. 2.3, 2.5)
CREATE TABLE execution_logs (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_id         UUID NOT NULL,
    project_id         UUID REFERENCES projects_cad(id) ON DELETE SET NULL,
    iteration          INTEGER,
    prompt             TEXT,
    generated_code     TEXT,
    sandbox_success    BOOLEAN,
    stdout             TEXT,
    stderr             TEXT,
    error_message      TEXT,
    error_traceback    TEXT,
    user_corrections   JSONB,
    log_payload        JSONB,
    evaluated_by_coach BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE execution_logs IS 'Strukturierte Interaktions-Logs für Meta-Coach-Auswertung';
COMMENT ON COLUMN execution_logs.log_payload IS 'Vollständiges JSON-Log gemäss SPEC Kap. 2.3/2.4';

CREATE INDEX idx_execution_logs_session ON execution_logs (session_id);
CREATE INDEX idx_execution_logs_project ON execution_logs (project_id);
CREATE INDEX idx_execution_logs_coach   ON execution_logs (evaluated_by_coach) WHERE evaluated_by_coach = FALSE;
CREATE INDEX idx_projects_cad_created   ON projects_cad (created_at DESC);

-- Chat-Unterhaltungen (Gemini-ähnlich: mehrere Chats, reaktivierbar)
CREATE TABLE conversations (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title      VARCHAR(255) NOT NULL DEFAULT 'Neue Unterhaltung',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE conversation_messages (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             VARCHAR(32) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content          TEXT NOT NULL,
    cad_session_id   VARCHAR(64),
    meta             JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE conversation_artifacts (
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

CREATE INDEX idx_conversation_messages_conv ON conversation_messages (conversation_id, created_at);
CREATE INDEX idx_conversation_artifacts_conv ON conversation_artifacts (conversation_id);
CREATE INDEX idx_conversations_updated ON conversations (updated_at DESC);
