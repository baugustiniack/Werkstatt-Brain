-- Kapitel 2/5 Migration: additive Schema-Erweiterungen für bereits laufende
-- Entwicklungsdatenbanken (idempotent via IF NOT EXISTS / CHECK). Für neue,
-- frische Deployments ist dies bereits vollständig in 01_schema.sql enthalten;
-- diese Datei existiert nur für den Live-Nachzug einer bestehenden Instanz.

ALTER TABLE projects_cad
    ADD COLUMN IF NOT EXISTS requirements_contract JSONB,
    ADD COLUMN IF NOT EXISTS stl_file_path VARCHAR(512);

ALTER TABLE execution_logs
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects_cad(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS iteration INTEGER,
    ADD COLUMN IF NOT EXISTS stdout TEXT,
    ADD COLUMN IF NOT EXISTS stderr TEXT,
    ADD COLUMN IF NOT EXISTS error_traceback TEXT;

CREATE INDEX IF NOT EXISTS idx_execution_logs_project ON execution_logs (project_id);

CREATE TABLE IF NOT EXISTS unprocessed_assets (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_path     VARCHAR(512) UNIQUE NOT NULL,
    file_hash     VARCHAR(64) NOT NULL,
    file_type     VARCHAR(50) NOT NULL
                  CHECK (file_type IN ('image', 'step', 'stl', 'f3d', 'pdf')),
    status        VARCHAR(50) NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'processing', 'indexed', 'failed')),
    vision_result JSONB,
    error_message TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at  TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unprocessed_assets_hash   ON unprocessed_assets (file_hash);
CREATE INDEX        IF NOT EXISTS idx_unprocessed_assets_status ON unprocessed_assets (status) WHERE status = 'pending';
