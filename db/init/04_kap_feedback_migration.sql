-- Migration für das Nutzer-Feedback zum Chapter-3/5-Frontend: additive
-- Schema-Erweiterungen für bereits laufende Entwicklungsdatenbanken
-- (idempotent via IF NOT EXISTS). Für neue, frische Deployments ist dies
-- bereits vollständig in 01_schema.sql enthalten; diese Datei existiert nur
-- für den Live-Nachzug einer bestehenden Instanz.

-- 1) unprocessed_assets -> generische Asset-Bibliothek (Dateien + manuelle
--    Einträge, unstrukturiert bis zur KI-/manuellen Strukturierung).
ALTER TABLE unprocessed_assets
    ALTER COLUMN file_path DROP NOT NULL,
    ALTER COLUMN file_hash DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'crawler',
    ADD COLUMN IF NOT EXISTS title VARCHAR(255),
    ADD COLUMN IF NOT EXISTS notes TEXT,
    ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]';

-- Bestehende CHECK-Constraints auf file_type/source dynamisch ersetzen, da ihr
-- automatisch generierter Name je nach Postgres-Version variieren kann.
DO $$
DECLARE
    con_name text;
BEGIN
    SELECT con.conname INTO con_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'unprocessed_assets'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%file_type%';
    IF con_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE unprocessed_assets DROP CONSTRAINT %I', con_name);
    END IF;
END $$;

ALTER TABLE unprocessed_assets
    ADD CONSTRAINT unprocessed_assets_file_type_check
    CHECK (file_type IN ('image', 'step', 'stl', 'f3d', 'pdf', 'manual', 'other'));

DO $$
DECLARE
    con_name text;
BEGIN
    SELECT con.conname INTO con_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'unprocessed_assets'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%source%';
    IF con_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE unprocessed_assets DROP CONSTRAINT %I', con_name);
    END IF;
END $$;

ALTER TABLE unprocessed_assets
    ADD CONSTRAINT unprocessed_assets_source_check
    CHECK (source IN ('crawler', 'upload', 'manual'));

-- Der bisherige UNIQUE-Index auf file_hash erlaubt keine mehrfachen NULLs auf
-- allen Postgres-Versionen zuverlässig, wenn er als reguläre UNIQUE-Spalten-
-- Constraint (statt partiellem Index) angelegt wurde -> auf partiellen Index
-- umstellen, der NULLs explizit ausschließt.
DROP INDEX IF EXISTS idx_unprocessed_assets_hash;
CREATE UNIQUE INDEX IF NOT EXISTS idx_unprocessed_assets_hash
    ON unprocessed_assets (file_hash) WHERE file_hash IS NOT NULL;

-- 2) UI-verwaltete Laufzeit-Einstellungen (z. B. API-Keys statt nur .env).
CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
