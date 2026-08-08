```
# 🛠️ System-Spezifikation: Autonomes Werkstatt-Brain & CAD/CAM-Agenten-System

---

# 📐 Kapitel 1: System-Vision, Progression & High-Level Architektur

### 1.1 Zielsetzung & Ganzheitlicher Anspruch
* **Werkstatt-Brain statt reiner Code-Generator:** Das System liefert nicht nur CAD- oder G-Code, sondern **ganzheitliche Fertigungskonzepte** (z. B. Materialauswahl, Werkzeugpfade, Fügetoleranzen), indem es Werkstatt-Daten, vorhandene Fräser, Holzbestände und Datenbank-Kontexte intelligent kombiniert.
* **Kontinuierliche Lernschleife (Meta-Coach):** Das System analysiert systematisch Erfolge, Fehlschläge und Sandbox-Logs, um Prompting-Regeln, Toleranzwerte und Code-Muster über die Zeit autonom zu verbessern.

### 1.2 Die 2-Phasen-Infrastruktur & Nahtlose Migration
To ensure immediate usability during early development, the architecture relies on strict environment decoupling:


```

┌─────────────────────────────────────────────────────────────────────────────┐

│ PHASE 1: Entwicklungs- & Testbetrieb (Lokaler Laptop) │

│ • Docker Compose Setup (FastAPI, Postgres, Qdrant, Redis, Sandbox) │

│ • KI-Processing: Reiner Cloud-API-Betrieb (Anthropic / OpenAI) │

└──────────────────────────────────────┬──────────────────────────────────────┘

│ (Migration via Docker Volume Export

│ & Git/Environment Switch)

▼

┌─────────────────────────────────────────────────────────────────────────────┐

│ PHASE 2: Zielarchitektur (Dedizierter Werkstatt-Server mit NVIDIA GPU) │

│ • Selbes Container-Setup, erweitert um Ollama Container (GPU Passthrough) │

│ • Hybrid-Processing: Cloud für Echtzeit-Eingaben, Lokale GPU für Nachtschicht│

└─────────────────────────────────────────────────────────────────────────────┘

```

* **Zero-Lock-in Migrationspfad:** Die vollständige Containerisierung (Docker Compose) garantiert, dass Datenbanken (Postgres, Qdrant), Log-Systeme und Backends mit einem einzigen Befehl 1:1 vom Entwicklungs-Laptop auf den späteren Server übertragen werden können.

### 1.3 Der zwei-stufige KI-Lifecycle (Interactive vs. Nightly Idle)
* **1. Tag- & Echtzeitbetrieb (Interactive Layer):**
  * Schnelle Antwortzeiten für den Benutzer (PWA / UI).
  * Cloud-APIs für komplexes 3D-Reasoning und Konzeptgenerierung.
  * *Lückenlose Protokollierung:* Jeder Input, generierter Code, Fräsparameter-Vorschlag, Sandbox-Fehler und User-Feedback landet in strukturierten JSON-Logfiles.
* **2. Leerlauf- & Nachtbetrieb (Autonomous Background Worker):**
  * **Meta-Coach Auswertung:** Die lokale KI analysiert nachts die Logfiles aller Tag-Vorgänge (Welche Verrundungen sind gescheitert? Welche Fräser-Drehzahlen führten zu Fehlern?).
  * **Datenbank-Synthese & Strukturierung:** Unstrukturierte Projektskizzen, Fotos und Freitext-Notizen werden von der lokalen KI verknüpft, dedupliziert, neu getaggt und in Wissens-Graphen / Vektor-Embeddings überführt.
  * **Kontext-Vormischung:** Generierung von kompakten "Lessons Learned"-Rulesets für den nächsten Tag.

### 1.4 Frontend-Architektur & Benutzer-Schnittstellen
Das Frontend wird als responsive **Progressive Web App (PWA)** konzipiert:
* **Das KI-Dialog-UI (Workstation):**
  * Multi-Modal Input (Sprache, Text, Skizzen/Fotos von Werkstücken).
  * 3D-Live-Preview von `build123d`-Geometrien direkt im Browser (via Three.js / WebGL / gLTF).
  * Anzeige von ganzheitlichen Fertigungskonzepten (inkl. Materialbedarfsliste, empfohlenen Fräsern und Schritt-für-Schritt-Anleitung).
* **Das Datenbank- & Werkstatt-Cockpit:**
  * Werkzeug- & Materialverwaltung (Fräser-Inventar, Holzarten, Restplatten-Lager).
  * Visualisierung der Wissensdatenbank (Knowledge Graph & Vektor-Suchmaske).
* **Das Meta-Coach / System-Insights UI:**
  * Übersicht über verarbeitete Log-Files und gelernte Faustregeln.
  * Einsicht in Sandbox-Fehlerstatistiken und System-Logs.

### 1.5 Technologisches Fundament (Tech Stack)
* **Backend:** Python (FastAPI), Async Task-Processing (Celery/APScheduler).
* **Datenbank-Layer:** PostgreSQL (Relational/Logs), Qdrant (Vektor-DB für Embeddings), Redis (Queue/Cache).
* **Execution & Sandbox:** Isolated Python Container für sichere Geometrie-Ausführung (`build123d` / OpenCASCADE).

### 1.6 Sicherheits- & Isolation-Architektur (Security & Safety)
* **Code Execution Sandboxing (Schutz vor LLM-Code-Injection):**
  * Der von der KI generierte Python-Code für `build123d` wird **niemals** direkt auf dem Host-System oder im Haupt-Backend ausgeführt.
  * Ausführung erfolgt in einem stark isolierten, netzwerkisolierten Docker-Container (*ephemeral container*) mit **Drop All Capabilities**, festgelegten **Resource Limits** (CPU/RAM/Timeout) und Read-Only-Filesystem.
* **Network & Access Security:**
  * **Local First:** Das System ist primär für das lokale Netzwerk (LAN/WLAN) konzipiert.
  * **Remote Access (Optional):** Ein geschützter Fernzugriff von unterwegs erfolgt ausschließlich über verschlüsselte Mesh-VPNs (z. B. Tailscale / WireGuard) und Token-basierte Authentifizierung (JWT).
* **Secrets & API-Key-Management:**
  * Keine Hardcoded Keys. Alle Cloud-API-Keys (Anthropic, OpenAI) und Datenbank-Passwörter liegen strikt in isolierten `.env`-Dateien und werden nicht in Repositories committet.
* **G-Code / Maschinen-Safety:**
  * Automatisch generierter G-Code oder kritische Fräsparameter erfordern vor der Übergabe an die Fräse immer ein explizites **Human-in-the-Loop Approval** (Bestätigung durch den Benutzer im UI nach Prüfung des Arbeitsraums).

---

# 🗄️ ## Kapitel 2: Datenmodell, Wissensrepräsentation & Der Meta-Coach

Das Datenmodell vereint strukturierte relationale Daten (PostgreSQL) für exakte Werkstatt-Parameter mit unstrukturierte Vektor-Wissen (Qdrant) für semantische Suchen, einem Knowledge Graph für Werkzeug-Material-Beziehungen sowie einer automatisierten Ingestion-Pipeline und dem autonomen **Meta-Coach** zur kontinuierlichen Systemoptimierung.

┌───────────────────────────────────────────────────────────────────────────────┐
│                                ARCHITEKTUR-OVERVIEW                           │
│                                                                               │
│  Relational (PostgreSQL)  ──► Stammdaten, Werkzeuge, Material, Logs, Assets   │
│  Vektor (Qdrant)          ──► CAD-Snippets, Bilder, Semantische Code-Suche   │
│  Knowledge Graph          ──► Verknüpfung: Fräser ──passt zu──► Material      │
│  Meta-Coach (Nachtarbeit) ──► Log-Analyse, Auto-Prompting, Regel-Updates     │
└───────────────────────────────────────────────────────────────────────────────┘


---

### 2.1 Relationale Datenbank (PostgreSQL)

PostgreSQL speichert präzise physikalische Parameter, Werkzeug-Spezifikationen, Lagerbestände, unstrukturierte Roh-Assets und den Ausführungsverlauf für die Log-Analyse.

#### 2.1.1 Relationales Schema (`db/init/01_schema.sql`)

```sql
-- 1. Werkzeug-Inventar (Schaftfräser, V-Nut-Fräser, Bohrer)
CREATE TABLE tools (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    tool_type VARCHAR(50) NOT NULL, -- e.g., 'flat_endmill', 'ball_nose', 'v_bit'
    diameter_mm NUMERIC(5, 2) NOT NULL,
    flute_length_mm NUMERIC(5, 2),
    shank_diameter_mm NUMERIC(5, 2),
    max_rpm INT,
    feed_rate_mm_min INT,
    status VARCHAR(50) DEFAULT 'ok', -- 'ok', 'worn', 'broken'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Material- & Vorratslager (Holzreste, Platten, Metallteile)
CREATE TABLE stock_materials (
    id SERIAL PRIMARY KEY,
    material_type VARCHAR(100) NOT NULL, -- e.g., 'oak', 'birch_plywood', 'aluminum'
    length_mm NUMERIC(6, 2) NOT NULL,
    width_mm NUMERIC(6, 2) NOT NULL,
    thickness_mm NUMERIC(6, 2) NOT NULL,
    grain_direction VARCHAR(50), -- e.g., 'longitudinal', 'transverse', 'none'
    location VARCHAR(100), -- e.g., 'Regal A', 'Restekiste unter Fräse'
    is_available BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. CAD-Projekte, Bibliotheken & Historieneinträge
CREATE TABLE projects_cad (
    id SERIAL PRIMARY KEY,
    project_name VARCHAR(255) NOT NULL,
    raw_prompt TEXT NOT NULL,
    requirements_contract JSONB NOT NULL,
    generated_code TEXT NOT NULL, -- build123d Quellcode
    step_file_path VARCHAR(512),
    stl_file_path VARCHAR(512),
    gcode_file_path VARCHAR(512),
    human_rating INT CHECK (human_rating BETWEEN 1 AND 5),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Unverarbeitete Dateien (Crawler Queue & Upload Ingestion)
CREATE TABLE unprocessed_assets (
    id SERIAL PRIMARY KEY,
    file_path VARCHAR(512) UNIQUE NOT NULL,
    file_hash VARCHAR(64) NOT NULL, -- SHA256 Duplikatschutz
    file_type VARCHAR(50) NOT NULL, -- 'image', 'step', 'stl', 'pdf'
    status VARCHAR(50) DEFAULT 'pending', -- 'pending', 'processing', 'indexed', 'failed'
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Ausführungs-Logs (Das Archiv für den Meta-Coach)
CREATE TABLE execution_logs (
    id SERIAL PRIMARY KEY,
    project_id INT REFERENCES projects_cad(id) ON DELETE CASCADE,
    session_id VARCHAR(100) NOT NULL,
    iteration INT NOT NULL,
    code_executed TEXT NOT NULL,
    sandbox_success BOOLEAN NOT NULL,
    stdout TEXT,
    stderr TEXT,
    error_traceback TEXT,
    user_corrections TEXT,
    evaluated_by_coach BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
2.2 Vektor-Datenbank (Qdrant) & Knowledge Base
Qdrant speichert unstrukturiertes Vektor-Wissen für die semantische Suche. Es werden lokale Embedding-Modelle (z. B. bge-m3, nomic-embed-text oder OpenCLIP) verwendet, um API-Kosten zu vermeiden.

2.2.1 Qdrant Collections
cad_snippets

Inhalt: Erfolgreich validierte build123d-Codebausteine für geometrische Primitives (z. B. Schwalbenschwanz-Zinkung, T-Nut-Aussparung).

Payload: function_name, category, code_body, python_dependencies, rating.

workshop_knowledge

Inhalt: Handbuch-Auszüge, Freitext-Notizen, Werkzeug-Datenblätter, gelerntes Wissen, Faustregeln.

Payload: title, source_document, content_chunk, tags.

visual_inventory

Inhalt: Multimodale Embeddings von Fräser-Fotos, Holzmaserungen, Werkstatt-Teilen und CAD-Renders.

Payload: asset_id, file_path, category, description.

2.3 Automatisierte Asset-Ingestion & Migrations-Pipeline
Damit die Werkstatt-Datenbank lebendig bleibt, verfügt das System über eine mehrstufige Erfassungsschnittstelle für digitale und analoge Daten:

2.3.1 Local PC-Asset Crawler (backend/app/services/crawler.py)
Hintergrund-Dienst: Scannet konfigurierbare lokale Ordner (z. B. Downloads, CAD-Bibliotheken) rekursiv nach Dateitypen (.step, .stl, .f3d, .png, .jpg).

Duplikatschutz: Hash-Prüfung per SHA256. Bereits indizierte Dateien werden übersprungen.

Queueing: Neue Funde landen mit Status pending in unprocessed_assets.

2.3.2 KI-Vision & Multimodale Klassifizierung (backend/app/services/vision_ingest.py)
Fotos oder gerenderte CAD-Vorschauen werden an ein Multimodal-Modell geschickt.

Extraction Output (JSON):

JSON
{
  "category": "tool",
  "tool_type": "v_bit",
  "estimated_dimensions": {
    "shank_diameter_mm": 6.0,
    "angle_deg": 60
  },
  "description": "60-Grad V-Nut Fräser für Gravuren",
  "tags": ["cnc", "v-carve", "gravur"]
}
Routing: Nach Verifizierung fließen Daten strukturiert in PostgreSQL (tools / stock_materials) und synchron als Vektor-Payload in Qdrant (visual_inventory).

2.3.3 CAD-Modell Migration ("1-Click Migration")
Nach erfolgreicher Sandbox-Validierung und Nutzerfreigabe im Frontend speichert der Endpunkt POST /api/v1/inventory/migrate-cad das Bauteil samt Parametern, .step und .stl fest in der Projektdatenbank und indiziert den Code-Snippet in Qdrant.

2.4 Log-Struktur für Interaktionszyklen
Jede Generierung hinterlässt ein detailliertes JSON-Log in PostgreSQL als Arbeitsgrundlage für den Meta-Coach:

JSON
{
  "log_id": "log_2026_08_07_001",
  "timestamp": "2026-08-07T13:10:00Z",
  "context": {
    "material": "Multiplex 18mm",
    "selected_tool": "8mm Nutfräser Z2",
    "target_object": "Adapterplatte CNC"
  },
  "prompt": "Erstelle eine Aussparung für eine 8mm T-Nut Schiene.",
  "generated_code": "with BuildPart() as p:\n    Box(100, 50, 18)\n    # ...",
  "execution_result": {
    "status": "SANDBOX_ERROR",
    "error_type": "ValueError",
    "error_message": "Radius 5.0 exceeds face boundary at fillet step",
    "attempts": 2
  },
  "user_feedback": {
    "corrected_manually": true,
    "user_comment": "Verrundung an den Kanten war zu groß für 18mm Plattenstärke."
  }
}
2.5 Der Meta-Coach (Der autonome Nachtarbeiter)
Ein via APScheduler / Celery Queue gesteuerter Hintergrundprozess, der im Leerlauf (z. B. nachts zwischen 02:00 und 05:00 Uhr oder per On-Demand-Trigger) das System analysiert und verbessert:

Fehler-Klassifizierung: Analyziert unerledigte Einträge in execution_logs (evaluated_by_coach = FALSE), gruppiert Syntax-Fehler, Geometrie-Konflikte (build123d-Exceptions) sowie Werkzeug-Fehlpassungen.

System-Prompt-Update: Erstellt und aktualisiert dynamische Regel-Dateien (z. B. rules_cnc_geometry.json), die tagsüber automatisch als System-Kontext vor den Generierungs-Prompt gehängt werden.

Graph- & Inventar-Deduplizierung: Bereinigt doppelte Material- und Werkzeugeinträge aus Bildeingaben und ordnet sie eindeutigen Kategorien im Knowledge Graph zu.

Offline-Vektorisierung: Wandelt neue Freitextnotizen, erfolgreiche CAD-Snippets und verarbeitete Fotos in Vektor-Embeddings um.

Lokale Modellausführung: Nutzt für die Synthese bevorzugt eine lokale GPU (über Ollama / Llama-3 / Qwen), um keine unnötigen Cloud-API-Kosten zu verursachen.

2.6 Datenbanksicherheit & Backup-Architektur
Zero Data Loss: Automatische nächtliche Dumps der PostgreSQL-Datenbank auf ein lokales NAS oder eine externe Festplatte.

Vektor-Snapshots: Regelmäßige Sicherung der Qdrant-Snapshots.

Datenschutz & Souveränität: Sensible Werkstatt-Daten, Pfade und interne Notizen verbleiben rein lokal auf dem System.
```



# 💬 Kapitel 3: KI-Interaktions- & Agenten-Konzept (LangGraph)



### 3.1 Die LangGraph State Machine & Agenten-Topologie

Das System ist als zielgerichteter, zyklischer Graph auf Basis von **LangGraph** aufgebaut.

Anstelle einer starr hierarchischen Kaskade wird das System als dynamisches Agenten-Netzwerk modelliert. Jeder Agent besitzt die Fähigkeit, über den LangGraph State einen Rücksprung (*Rollback*) zu jedem anderen Agenten auszulösen.

```

```

```
          ┌──────────────────────────────────────────┐
          │       0. Master / Supervisor Agent       │
          └────────────────────┬─────────────────────┘
                               │ (State Monitor & Router)
                               ▼
```

   ┌───────────────────────────────┼───────────────────────────────┐
   ▼                               ▼                               ▼

```

┌───────────────┐             ┌─────────────────┐             ┌─────────────────┐
│ Concept       │◄───────────►│ Inventory &     │◄───────────►│ 3D Builder      │
│ Builder       │             │ Data Manager    │             │ (`build123d`)   │
└───────┬───────┘             └─────────────────┘             └────────┬────────┘
        │                              ▲                               │
        │                              │ (Fehlermeldungen /            │
        │                              │  Physics Violations)          │
        │                              ▼                               │
        └──────────────────────►┌───────────────┐◄─────────────────────┘
                                │ Validator     │
                                └───────────────┘

```



### 3.2 Die Spezialisierten Agenten-Rollen (Graph Nodes)

1. **Master / Supervisor Agent (Orchestrator):** Dynamische Zusammenstellung des benötigten Agenten-Teams je nach Komplexität, Überwachung von State, Abbruch- und Eskalationsbedingungen.
2. **Concept Builder (Produktdesigner & System-Architekt):**
  - Übersetzt das Nutzerproblem in ein ästhetisches, funktionales und ergonomisches Gesamtkonzept.
  - Synthetisiert Nutzerwünsche mit Handwerks-Best-Practices (Fügearten, Stabilität, Ästhetik).
  - Iteriert mit dem User, bis das Konzept begeistert, bevor tief in Code oder Fräsparameter eingetaucht wird.
3. **Inventory & Data Manager (Der Werkstatt-Integrator):**
  - Realitätsabgleich mit der physischen Welt (PostgreSQL Werkzeug- & Materialdaten).
  - Holt historische Erfahrungen, User-Präferenzen und Material-Eigenschaften aus Qdrant.
4. **3D Builder (CAD- & Code-Synthesizer):**
  - Exakte mathematische Translate-Schicht vom Konzept in `build123d`-Python-Code.
  - Setzt Maße, Radien und Passungen präzise in Geometrie-Operationen um.
5. **Validator (Safety, Physics & Feasibility Checker):**
  - Lässt den Code in der isolierten Docker-Sandbox laufen.
  - Prüft Wandstärken, Mindestradien, Kollisionen und Werkzeugbelastungen.
  - Meldet Code-Fehler an den *3D Builder* oder Design-Fehler an den *Concept Builder* zurück.



### 3.3 Spezifikation des Anforderungskatalogs (Requirements Contract)

Bevor der 3D Builder Code generiert, wird ein Anforderungskatalog (JSON-Objekt) durch den *Concept Builder* erstellt:

- **Funktionale Geometrie:** Primäre Abmessungen ($X, Y, Z$), Radien, Wandstärken, Passungen/Toleranzen.
- **Material- & Werkzeug-Constraints:** Vorgesehene Holzart, Plattenstärke, Fräserdurchmesser.
- **Fertigungs-Feature-Liste:** Z. B. Tasche, Falz, Durchgangsbohrung, Schwalbenschwanz, T-Nut.
- **Gap-Analyse:** Ausgewiesene offene Punkte, die Bestätigung erfordern.



### 3.4 Die 3 Haupt-Iterationsschleifen (Loop Mechanics)

- **🔄 Schleife 1: Design- & Machbarkeits-Schleife (Concept ↔ Inventory):** Concept Builder entwickelt Idee; Inventory Manager gleicht ab und liefert vorhandene Bestände (z. B. Plattenstärke, Fräser).
- **🔄 Schleife 2: CAD-Geometrie-Schleife (3D Builder ↔ Concept Builder):** 3D Builder stößt beim Coden auf mathematische/topologische Konflikte (z. B. Restwandstärke zu gering) und sendet ein Refinement Request an den Concept Builder.
- **🔄 Schleife 3: Test- & Validierungs-Schleife (Validator ↔ 3D Builder / Concept Builder):**
  - *Code-Fehler:* Validator sendet Traceback an den 3D Builder (schnelle lokale Korrektur).
  - *Physik-/Design-Fehler:* Validator meldet Problem an den Concept Builder zur grundlegenden Überarbeitung.



### 3.5 Autonomer Rücksprung vs. User-Eskalation (Decider & Interrupt Node)

- **Autonome Justage:** Parameter-Anpassung innerhalb systeminterner Toleranzen (z. B. Verringerung Fasenbreite von $1{,}0\text{ mm}$ auf $0{,}8\text{ mm}$).
- **User-Eskalation (**`interrupt()`**):** Bei fundamentalen Designentscheidungen, Stabilitätsproblemen oder fehlenden Werkzeugen pausiert LangGraph den State und bindet den User über das UI ein.



### 3.6 Inter-Agent Communication Protocol

Kommunikation erfolgt über ein strikt typisiertes JSON-Format:

JSON

```

{
  "sender_agent": "Inventory_Manager",
  "recipient_agent": "3D_Builder",
  "payload_type": "CONTEXT_INJECTION",
  "data": {
    "recommended_tools": [
      {"id": "t_08_vhm", "name": "8mm VHM Nutfräser", "max_depth_mm": 22.0}
    ],
    "stock_constraints": {
      "material": "Multiplex",
      "thickness_mm": 18.0
    },
    "relevant_rules": [
      "Verrundungsradius R muss kleiner als 4.0mm sein für 8mm Fräser."
    ]
  }
}

```



# 🔌 Kapitel 4: Schnittstellen, Werkstatt-Integration & Datenaustausch



### 4.1 Export-Formate & CAD/CAM-Schnittstellen

- **Native Parametrik (**`.py`**):** Primary Artifact. `build123d`-Python-Code zur Wiederverwendung und Variablen-Anpassung.
- **Neutrale 3D-Daten (**`.step` **/** `.iges`**):** Standard-Export für externe CAD/CAM-Systeme.
- **Mesh- & Vorschau-Daten (**`.stl` **/** `.gltf`**):** Leichtgewichtige Formate für den WebGL 3D-Viewer in der PWA.
- **Maschinen-Code (**`.gcode` **/** `.nc`**):** Postprozessierte G-Code-Dateien für die Frässteuerung (GRBL, LinuxCNC, Estlcam).



### 4.2 Integration des Werkzeug- & Material-Inventars

- **Tool Library Specs:** Typ, Durchmesser ($D$), Schneidenlänge ($L_c$), Gesamtlänge ($L$), maximale Schnitttiefe ($a_p$), Vorschub ($f_z$), Drehzahl ($n_{\max}$), Standzeit-Protokollierung.
- **Stock Management:** Erfassung von Vollformat-Platten und Reststücken inkl. Abmessungen ($X, Y, Z$), Materialtyp und Faserverlauf/Maserung. Reststück-Nesting wird bevorzugt.



### 4.3 Anbindung der CNC-Maschine & Maschinensicherheit

- **Postprozessor-Abstraktion:** Anpassung des CAM-Codes an spezifische Maschinensprachen.
- **Machine Safety Guard:** Zero-Crossing Check (Prüfung gegen Verfahrwege/Endschalter der Fräse) und Vorschub-/Drehzahl-Sanity Check.
- **Physical Gatekeeping:** Kein autonomes Anlaufen der Maschine. G-Code-Transfer oder Ausführung erfordert immer ein manuelles **Human Approval** im PWA-UI.



### 4.4 Externe KI- & Cloud-Schnittstellen

- **LLM Provider Abstraktion:** Universelles Interface für Anthropic Claude API, OpenAI API und lokale Ollama/vLLM-Instanzen.
- **Fallback-Logik:** Automatisches Umschalten auf die lokale GPU bei Internetausfall.
- **Token- & Budget-Monitoring:** Protokollierung der API-Kosten im Dashboard.

```
## Kapitel 5: Frontend, User Interface & Asset Management (PWA)

Das Frontend ist als reaktive, werkstatttaugliche Progressive Web App (PWA) konzipiert. Es dient als primäre Schnittstelle zur Interaktion mit den Agenten, zur Live-Überwachung von Sandbox-Prozessen, zur Visualisierung von 3D-Modellen im Browser und zur Verwaltung des Werkstatt-Inventars.

---

### 5.1 Architektur & Tech-Stack

* **Framework:** React / Next.js (oder Vite + React) mit TypeScript und Tailwind CSS für ein klares, schnelles und mobiles Layout.
* **3D-Visualisierung:** `Three.js` via `@react-three/fiber` und `@react-three/drei` zum hardwarebeschleunigten Rendern von STL- und STEP-Dateien direkt im WebGL-Canvas.
* **Echtzeit-Kommunikation:** WebSockets oder Server-Sent Events (SSE) für das Streaming von LangGraph-State-Änderungen, Konsolen-Logs und Sandbox-Tracebacks in Echtzeit.
* **PWA & Mobile-Support:** Offline-Fähigkeit, Touch-Optimierung und Kamera-Anbindung für die direkte Nutzung per Tablet oder Smartphone an der CNC-Fräse/Werkbank.

---

### 5.2 Core UI Components & Dashboard Layout

Das Dashboard gliedert sich in vier Hauptbereiche:

┌───────────────────────────────────────────────────────────────────────────────┐
│                           WERKSTATT-BRAIN DASHBOARD                           │
├───────────────────────────────────────┬───────────────────────────────────────┤
│ 1. AGENT COMMAND CENTER & PROMPT      │ 3. 3D WEBGL MODEL VIEWER              │
│    [ Eingabefeld für Bauteilwunsch ]  │    [ Interaktiver 3D Canvas (.stl) ]  │
│    [ Material- & Fräser-Quickselect ] │    [ Wireframe / Maße / Schnitte ]    │
│    [ Status-Badge: BUILDING_3D... ]   │    [ 💾 In Inventar-DB migrieren ]    │
├───────────────────────────────────────┼───────────────────────────────────────┤
│ 2. LIVE AGENT TRACE & LOGS            │ 4. INVENTORY & ASSET INGESTION        │
│    [ Visualisierung LangGraph-Nodes ] │    [ Drag & Drop Upload Zone ]        │
│    [ Terminal-Output & Code-Editor ]  │    [ KI-Vision Kategorisierung ]      │
│    [ Escalation / Human Approval ]    │    [ PC-Crawler Status & Queue ]      │
└───────────────────────────────────────┴───────────────────────────────────────┘


#### 1. Agent Command Center (Prompt & Control Bar)
* **Prompt-Eingabe:** Freitextfeld zur Beschreibung des gewünschten Bauteils (z. B. *"Erstelle eine Halterung für meine T-Nut-Schiene mit 8mm Senkkopfbohrung"*).
* **Parameter-Quickselect:** Schnellfilter zur Vorgabe von Fräsern (`tools`) oder Holzresten (`stock_materials`) direkt aus PostgreSQL.
* **Status-Indikator:** Live-Badge zeigt den aktuellen Graph-Knoten an (`CONCEPT`, `FETCHING_INVENTORY`, `BUILDING_3D`, `VALIDATING_SANDBOX`, `ESCALATION`).

#### 2. Live Agent Trace & Execution Console
* **Graph-Visualizer:** Visuelle Darstellung der aktiven LangGraph-Nodes und Iterationsschleifen.
* **Live Console:** Einklappbares Terminal mit Syntax-Highlighting für den von `builder_3d` generierten `build123d`-Code sowie Compiler-Logs aus der Sandbox.
* **Human-in-the-Loop Dialog:** Pop-up bei unlösbaren Geometriekonflikten oder fehlendem Material (z. B. *"Kein 8mm Nutfräser verfügbar. Soll 6mm Schaftfräser verwendet werden?"*).

#### 3. 3D WebGL Model Viewer
* **Interactive Canvas:** Rendert das aus der Sandbox exportierte `.stl`-Modell.
* **Viewer Controls:** Orbit Controls (Drehen, Zoomen, Pan), Drahtgitter-Modus (Wireframe), Bounding-Box-Dimensionen und Explosionsansichten.
* **Download Center:** Buttons zum Exportieren von `.STEP`, `.STL` und `.NC` (G-Code).
* **1-Click DB-Migration:** Button *"In Inventar-DB speichern"*, der das fertig generierte Modell als festen Eintrag in PostgreSQL und Qdrant überführt.

#### 4. Inventory, Upload & Asset Ingestion Center
* **Werkzeug- & Materialverwaltung:** Tabellenansicht und Editierfenster für Bestände.
* **Manual Upload Zone:** Drag-and-Drop Schnittstelle für Smartphone-Fotos von Fräsern/Holzresten oder manuell hochgeladene CAD-Dateien.
* **KI-Vision Preview:** Zeigt den automatischen Kategorisierungsvorschlag der KI an (Kategorie, geschätzte Maße, Werkzeugtyp) und erlaubt die Bestätigung mit einem Klick.
* **PC-Crawler Control:** Statusanzeige für den Hintergrund-Scanner mit Ansicht der Queue (`unprocessed_assets`).

---

### 5.3 API-Schnittstellen für das Frontend (`backend/app/api/`)

| Endpunkt | Methode | Beschreibung |
| :--- | :--- | :--- |
| `/api/v1/cad/generate` | `POST` | Startet den LangGraph-Workflow mit dem User-Prompt |
| `/api/v1/cad/stream/{session_id}` | `WS / SSE` | Streamt Live-Logs, Graph-States und Sandbox-Ergebnisse |
| `/api/v1/inventory/tools` | `GET / POST` | Liest und aktualisiert die Werkzeug-Datenbank (`tools`) |
| `/api/v1/inventory/materials` | `GET / POST` | Liest und aktualisiert das Materiallager (`stock_materials`) |
| `/api/v1/inventory/upload` | `POST` | Nimmt Bilder/CAD-Dateien entgegen und triggert die KI-Vision-Pipeline |
| `/api/v1/inventory/migrate-cad` | `POST` | Überführt ein Sandbox-Generierungsergebnis fest in `projects_cad` und Qdrant |
| `/api/v1/crawler/scan` | `POST` | Triggert einen manuellen Durchlauf des PC-Asset-Crawlers |

---

### 5.4 Progressive Web App (PWA) Spezifikation

* **Web App Manifest (`public/manifest.json`):** Definierte Icons, Werkstatt-Theme (`#1e1e2e`), Display-Modus `standalone`.
* **Kamera-Integration:** Nutzen der HTML5 File API / MediaDevices API für direkte Fotoaufnahmen von Werkzeugen und Materialresten per Tablet/Smartphone.
* **Service Worker:** Caching von Frontend-Assets für schnelle Ladezeiten in der Werkstatt.
```

