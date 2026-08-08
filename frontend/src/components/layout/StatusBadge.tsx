import type { AgentNodeName } from "../../api/types";
import type { CadRunStatus } from "../../api/useCadStream";

const NODE_LABELS: Record<AgentNodeName, string> = {
  supervisor: "SUPERVISOR",
  concept_builder: "CONCEPT",
  inventory_manager: "FETCHING_INVENTORY",
  builder_3d: "BUILDING_3D",
  validator: "VALIDATING_SANDBOX",
  human_escalation: "ESCALATION",
};

const STATUS_COLORS: Record<CadRunStatus, string> = {
  idle: "bg-workshop-border text-workshop-muted",
  connecting: "bg-workshop-accent/20 text-workshop-accent animate-pulse",
  running: "bg-workshop-accent/20 text-workshop-accent",
  escalation: "bg-workshop-warning/20 text-workshop-warning",
  completed: "bg-workshop-success/20 text-workshop-success",
  failed: "bg-workshop-danger/20 text-workshop-danger",
  cancelled: "bg-workshop-warning/20 text-workshop-warning",
  error: "bg-workshop-danger/20 text-workshop-danger",
};

export function StatusBadge({ status, currentNode }: { status: CadRunStatus; currentNode: AgentNodeName | null }) {
  const label =
    status === "escalation"
      ? "ESCALATION"
      : status === "completed"
        ? "COMPLETED"
        : status === "cancelled"
          ? "CANCELLED"
          : status === "failed"
            ? "FAILED"
            : status === "error"
              ? "ERROR"
              : status === "idle"
                ? "IDLE"
                : currentNode
                  ? NODE_LABELS[currentNode]
                  : "STARTING...";

  return (
    <span className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-mono font-semibold tracking-wide ${STATUS_COLORS[status]}`}>
      {label}
    </span>
  );
}
