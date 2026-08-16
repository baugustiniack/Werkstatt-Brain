import type { AgentNodeName } from "../../api/types";

const NODE_ORDER: { id: AgentNodeName; label: string }[] = [
  { id: "supervisor", label: "Supervisor" },
  { id: "flexible_specialist", label: "Flexible\nSpecialist" },
  { id: "interior_architect", label: "Innen-\narchitekt" },
  { id: "custom_agent_1", label: "Leer-\nAgent 1" },
  { id: "custom_agent_2", label: "Leer-\nAgent 2" },
  { id: "vv_manager", label: "V&V\nManager" },
  { id: "concept_builder", label: "Concept\nBuilder" },
  { id: "concept_critic", label: "Konzept-\nKritiker" },
  { id: "inventory_manager", label: "Inventory\nSpecialist" },
  { id: "fertigung_specialist", label: "Fertigungs\nSpecialist" },
  { id: "builder_3d", label: "3D\nBuilder" },
  { id: "validator", label: "Validator" },
  { id: "montage_manager", label: "Montage\nManager" },
  { id: "human_escalation", label: "Human\nEscalation" },
];

/** Einfache Visualisierung der aktiven LangGraph-Nodes (SPEC Kap. 3.1, 5.2 Panel 2). */
export function GraphVisualizer({ currentNode }: { currentNode: AgentNodeName | null }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-workshop-border bg-workshop-bg p-3">
      {NODE_ORDER.map((node, index) => {
        const isActive = node.id === currentNode;
        return (
          <div key={node.id} className="flex items-center gap-2">
            <div
              className={`rounded-md border px-2 py-1 text-center text-[10px] font-mono leading-tight whitespace-pre-line transition ${
                isActive
                  ? "border-workshop-accent bg-workshop-accent/20 text-workshop-accent"
                  : "border-workshop-border text-workshop-muted"
              }`}
            >
              {node.label}
            </div>
            {index < NODE_ORDER.length - 1 && <span className="text-workshop-muted">→</span>}
          </div>
        );
      })}
    </div>
  );
}
