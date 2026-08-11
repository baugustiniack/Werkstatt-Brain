/**
 * TypeScript-Gegenstücke zu den Backend-Pydantic-/SQLAlchemy-Modellen
 * (siehe backend/app/api/endpoints/*.py, agents/state.py). Bewusst als
 * eigenständige Typen gepflegt (kein Code-Gen), da das Backend aktuell
 * kein OpenAPI-Client-Gen-Setup hat.
 */

// ── agents/state.py ─────────────────────────────────────────────────────────

export interface FunctionalGeometry {
  dimensions_mm: { x: number; y: number; z: number };
  radii_mm: number | null;
  wall_thickness_mm: number | null;
  tolerances_mm: number;
}

export interface MaterialToolConstraints {
  material_type: string;
  plate_thickness_mm: number;
  tool_diameter_mm: number;
}

/** Ein einzelnes, unabhängig fertigbares CNC-Teil innerhalb einer (ggf.
 * mehrteiligen) Anfrage, z.B. ein Möbelstück eines Zimmer-Layouts. */
export interface ConceptPart {
  name: string;
  functional_geometry: FunctionalGeometry;
  material_tool_constraints: MaterialToolConstraints;
  manufacturing_features: string[];
  gap_analysis: string[];
  position_xy_mm?: { x: number; y: number };
}

export interface RequirementsContract {
  project_title: string;
  raw_prompt?: string;
  parts: ConceptPart[];
  gap_analysis: string[];
}

/** Von agents/nodes/supervisor.py in `completed_parts` gesichertes Ergebnis
 * eines abgeschlossenen Teils der Mehrteil-Ausarbeitung. */
export interface CompletedPart {
  name: string;
  part_contract: ConceptPart;
  stock_and_tool_context: StockAndToolContext | null;
  generated_code: string | null;
  sandbox_result: SandboxResult | null;
}

export interface RecommendedTool {
  id: string;
  name: string;
  diameter_mm: number;
  max_rpm: number | null;
  exact_match: boolean;
}

export interface StockConstraints {
  id: string;
  material_type: string;
  dimensions_xyz_mm: Record<string, number>;
  grain_direction: string | null;
}

export interface StockAndToolContext {
  sender_agent: string;
  recipient_agent: string;
  payload_type: string;
  data: {
    recommended_tools: RecommendedTool[];
    stock_constraints: StockConstraints | Record<string, never>;
    relevant_rules: string[];
  };
}

export type SandboxStatus = "SUCCESS" | "SANDBOX_ERROR";

export interface SandboxResult {
  status: SandboxStatus;
  error_type: string | null;
  error_message: string | null;
  traceback: string | null;
  stdout: string | null;
  export_paths: string[];
}

/** Freigabe-Gates: Konzept oder V&V (Frage / Bestätigung). */
export interface EscalationPayload {
  reason: string;
  requirements_contract: RequirementsContract | null;
  stock_and_tool_context: StockAndToolContext | null;
  sandbox_result: SandboxResult | null;
  iteration_count: number;
  concept_sketch_svg?: string | null;
  concept_image_url?: string | null;
  vv_requirements?: {
    title?: string;
    phase?: string;
    summary?: string;
    requirements?: Array<{
      id?: string;
      text?: string;
      priority?: string;
      status?: string;
    }>;
    open_questions?: string[];
    acceptance_criteria?: string[];
    needs_user_alignment?: boolean;
  } | null;
  open_questions?: string[];
  summary?: string | null;
  /** Einzelne V&V-Klärungsfrage */
  question?: string;
  question_index?: number;
  question_total?: number;
  answered_so_far?: Array<{ question?: string; answer?: string }>;
  draft_title?: string | null;
  draft_summary?: string | null;
  phase?: string;
  qa_answers?: Array<{ question?: string; answer?: string }>;
}

/** Entscheidung des Nutzers auf eine Freigabe-Eskalation. */
export type ConceptDecision =
  | { decision: "approve" }
  | { decision: "revise"; feedback: string }
  | { decision: "answer"; answer: string };

// ── backend/app/api/endpoints/cad.py ────────────────────────────────────────

export type WorkflowStatus = "completed" | "failed" | "human_approval_required" | "pending";

export interface CadWorkflowResult {
  session_id: string;
  status: WorkflowStatus;
  generated_code: string | null;
  sandbox_result: SandboxResult | null;
  requirements_contract: RequirementsContract | null;
  stock_and_tool_context: StockAndToolContext | null;
  escalation: EscalationPayload | null;
  iteration_count: number;
  concept_sketch_svg?: string | null;
  concept_image_url?: string | null;
  completed_parts?: CompletedPart[] | null;
  current_part_index?: number;
  total_parts?: number;
  cancelled?: boolean;
  agent_transcript?: AgentTranscriptEntry[] | null;
  montage_result?: Record<string, unknown> | null;
  assembly_plan?: Record<string, unknown> | null;
  assembly_manual?: Record<string, unknown> | null;
}

export interface AgentTranscriptEntry {
  ts: string;
  agent: string;
  event: string;
  summary: string;
  detail?: Record<string, unknown>;
  to_agent?: string;
}

/** Node-Namen der LangGraph-Topologie (agents/graph.py). */
export type AgentNodeName =
  | "supervisor"
  | "flexible_specialist"
  | "custom_agent_1"
  | "custom_agent_2"
  | "vv_manager"
  | "concept_builder"
  | "inventory_manager"
  | "fertigung_specialist"
  | "montage_manager"
  | "builder_3d"
  | "validator"
  | "human_escalation";

export type CadStreamMessage =
  | { type: "node_update"; node: AgentNodeName; state: Record<string, unknown> }
  | { type: "escalation"; escalation: EscalationPayload }
  | { type: "final"; result: CadWorkflowResult }
  | { type: "cancelled"; session_id: string }
  | { type: "error"; error: string };

// ── backend/app/api/endpoints/inventory.py ──────────────────────────────────

export type ToolStatus = "neu" | "verschlissen" | "abgebrochen";

export interface Tool {
  id: string;
  name: string;
  diameter_mm: number;
  flute_length_mm: number | null;
  max_rpm: number | null;
  feed_rate_mm_min: number | null;
  status: ToolStatus;
}

export interface StockMaterial {
  id: string;
  material_type: string;
  dimensions_xyz_mm: Record<string, number>;
  grain_direction: string | null;
  notes: string | null;
}

export type AssetFileType = "image" | "step" | "stl" | "f3d" | "pdf" | "manual" | "other";
export type AssetStatus = "pending" | "processing" | "indexed" | "failed";
export type AssetSource = "crawler" | "upload" | "manual";

export interface UnprocessedAsset {
  id: string;
  file_path: string;
  file_hash: string;
  file_type: AssetFileType;
  status: AssetStatus;
  vision_result: Record<string, unknown> | null;
  error_message: string | null;
  discovered_at: string;
  processed_at: string | null;
}

export interface CrawlerQueueResponse {
  items: UnprocessedAsset[];
  total: number;
  pending: number;
}

export interface AssetUploadResponse {
  asset_id: string;
  file_path: string;
  file_type: string;
  status: string;
  duplicate: boolean;
  vision_result: Record<string, unknown> | null;
  created_record: Record<string, unknown> | null;
}

// ── unstrukturierte Asset-Bibliothek (Nutzer-Feedback) ──────────────────────

export interface InventoryItem {
  id: string;
  title: string | null;
  file_name: string | null;
  file_path: string | null;
  file_type: AssetFileType;
  source: AssetSource;
  status: AssetStatus;
  tags: string[];
  vision_result: Record<string, unknown> | null;
  /** @deprecated Alias für ai_notes */
  notes: string | null;
  user_notes: string | null;
  ai_notes: string | null;
  error_message: string | null;
  discovered_at: string;
  processed_at: string | null;
}

export interface InventoryItemListResponse {
  items: InventoryItem[];
  total: number;
  pending: number;
}

export interface ManualEntryCreateRequest {
  title: string;
  notes?: string;
  user_notes?: string;
  tags?: string[];
  auto_process?: boolean;
}

export interface InventoryItemUpdateRequest {
  title?: string;
  notes?: string;
  user_notes?: string;
  ai_notes?: string;
  tags?: string[];
}

export interface ProcessPendingResponse {
  processed: number;
  indexed: number;
  failed: number;
  skipped?: number;
}

export interface ConceptToInventoryRequest {
  session_id: string;
  conversation_id?: string | null;
  title?: string | null;
  auto_process?: boolean;
}

export interface ConceptToInventoryResponse {
  asset_id: string;
  title: string | null;
  status: string;
  inventory_file_url: string;
  conversation_artifact_url: string | null;
  duplicate: boolean;
}

// ── UI-verwaltete API-Key-Settings ──────────────────────────────────────────

export interface ApiKeyStatusResponse {
  anthropic_configured: boolean;
  openai_configured: boolean;
  cursor_configured: boolean;
  llm_provider: LlmProviderChoice;
  active_provider: "anthropic" | "cursor" | "none" | string;
}

export type LlmProviderChoice = "auto" | "anthropic" | "cursor";

export interface CadMigrateResponse {
  project_id: string;
  project_name: string;
  step_file_path: string | null;
  stl_file_path: string | null;
  qdrant_indexed: boolean;
}

export interface CrawlerScanResponse {
  scanned_paths: string[];
  files_seen: number;
  new_assets: number;
  duplicates_skipped: number;
  errors: string[];
}
