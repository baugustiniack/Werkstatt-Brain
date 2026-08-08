-- Dev-Seed-Daten für lokale Tests des Agenten-Workflows (SPEC Kap. 3, Beispiel Kap. 3.6).
-- Hinweis: Init-Skripte laufen nur beim allerersten Start eines LEEREN Postgres-Volumes.

INSERT INTO tools (name, diameter_mm, flute_length_mm, max_rpm, feed_rate_mm_min, status) VALUES
    ('8mm VHM Nutfräser Z2', 8.0, 22.0, 18000, 2400.0, 'neu'),
    ('6mm VHM Schaftfräser Z1', 6.0, 20.0, 20000, 1800.0, 'neu'),
    ('4mm VHM Schaftfräser Z2', 4.0, 12.0, 24000, 1200.0, 'verschlissen');

INSERT INTO stock_materials (material_type, dimensions_xyz_mm, grain_direction, notes) VALUES
    ('Multiplex', '{"x": 1220, "y": 610, "z": 18}', 'längs', 'Vollformat-Reststück'),
    ('Fichte', '{"x": 800, "y": 400, "z": 20}', 'quer', 'Massivholz-Bohle');
