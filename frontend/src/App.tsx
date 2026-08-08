import { useCallback, useEffect, useMemo, useState } from "react";

import { apiBaseUrl } from "./api/client";
import { useCadStream } from "./api/useCadStream";
import { DashboardLayout, type DashboardTab } from "./components/layout/DashboardLayout";
import { ConversationPanel } from "./components/conversation/ConversationPanel";
import { EscalationDialog } from "./components/agentTrace/EscalationDialog";
import { ModelViewer, type ModelViewerPart } from "./components/modelViewer/ModelViewer";
import { DownloadCenter } from "./components/modelViewer/DownloadCenter";
import { MigrateButton } from "./components/modelViewer/MigrateButton";
import { AssetLibrary } from "./components/inventory/AssetLibrary";
import { UploadDropzone } from "./components/inventory/UploadDropzone";
import { CrawlerPanel } from "./components/inventory/CrawlerPanel";
import { LoggingPanel } from "./components/logging/LoggingPanel";
import { AgentWorkflowPanel } from "./components/workflow/AgentWorkflowPanel";
import type { ConversationArtifact } from "./hooks/useConversations";

function partsFromArtifacts(artifacts: ConversationArtifact[]): ModelViewerPart[] {
  const stls = artifacts
    .filter((a) => a.kind === "stl")
    .sort((a, b) => (a.part_index ?? 0) - (b.part_index ?? 0));
  return stls.map((a) => ({
    name: a.label ?? `Teil ${(a.part_index ?? 0) + 1}`,
    stlUrl: `${apiBaseUrl()}${a.url}`,
  }));
}

function App() {
  const [prompt, setPrompt] = useState("");
  const [selectedPartIndex, setSelectedPartIndex] = useState(0);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversationArtifacts, setConversationArtifacts] = useState<ConversationArtifact[]>([]);
  const [activeTab, setActiveTab] = useState<DashboardTab>("workspace");
  const cad = useCadStream();

  const onArtifactsChange = useCallback((artifacts: ConversationArtifact[]) => {
    setConversationArtifacts(artifacts);
  }, []);

  const liveParts: ModelViewerPart[] =
    cad.sessionId && cad.completedParts.length > 0
      ? cad.completedParts.map((part, idx) => ({
          name: part.name,
          stlUrl: `${apiBaseUrl()}/api/v1/cad/download/${cad.sessionId}/stl?part_index=${idx}`,
        }))
      : [];

  const persistedParts = useMemo(() => partsFromArtifacts(conversationArtifacts), [conversationArtifacts]);

  const parts = liveParts.length > 0 ? liveParts : persistedParts;
  const hasAnyPart = parts.length > 0;
  const singleStlUrl = hasAnyPart ? parts[Math.min(selectedPartIndex, parts.length - 1)].stlUrl : null;

  const stepArtifact = conversationArtifacts.find(
    (a) => a.kind === "step" && (a.part_index ?? 0) === selectedPartIndex,
  );
  const stlArtifact = conversationArtifacts.find(
    (a) => a.kind === "stl" && (a.part_index ?? 0) === selectedPartIndex,
  );

  useEffect(() => {
    setSelectedPartIndex(Math.max(parts.length - 1, 0));
  }, [parts.length]);

  const showGenericEscalation =
    cad.escalation && cad.escalation.reason !== "concept_approval" ? cad.escalation : null;

  return (
    <>
      <DashboardLayout
        activeTab={activeTab}
        onTabChange={setActiveTab}
        workspace={
          <div className="grid h-full min-h-0 grid-cols-1 gap-3 lg:grid-cols-2">
            <section className="flex min-h-0 flex-col rounded-lg border border-workshop-border bg-workshop-panel">
              <header className="border-b border-workshop-border px-4 py-2">
                <h2 className="text-xs font-mono font-semibold tracking-widest text-workshop-muted uppercase">
                  Unterhaltung
                </h2>
              </header>
              <div className="min-h-0 flex-1 overflow-hidden p-4">
                <ConversationPanel
                  cad={cad}
                  prompt={prompt}
                  onPromptChange={setPrompt}
                  activeConversationId={activeConversationId}
                  onActiveConversationIdChange={setActiveConversationId}
                  onArtifactsChange={onArtifactsChange}
                />
              </div>
            </section>
            <section className="flex min-h-0 flex-col rounded-lg border border-workshop-border bg-workshop-panel">
              <header className="border-b border-workshop-border px-4 py-2">
                <h2 className="text-xs font-mono font-semibold tracking-widest text-workshop-muted uppercase">
                  3D Model Viewer
                </h2>
              </header>
              <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
                <div className="min-h-0 flex-1">
                  <ModelViewer
                    stlUrl={singleStlUrl}
                    parts={parts}
                    selectedPartIndex={selectedPartIndex}
                    onSelectPartIndex={setSelectedPartIndex}
                  />
                </div>
                <DownloadCenter
                  sessionId={liveParts.length > 0 ? cad.sessionId : null}
                  partIndex={hasAnyPart ? selectedPartIndex : undefined}
                  stepUrl={liveParts.length === 0 && stepArtifact ? `${apiBaseUrl()}${stepArtifact.url}` : null}
                  stlUrl={liveParts.length === 0 && stlArtifact ? `${apiBaseUrl()}${stlArtifact.url}` : null}
                />
                <MigrateButton
                  sessionId={cad.sessionId}
                  canMigrate={liveParts.length > 0}
                  partIndex={liveParts.length > 0 ? selectedPartIndex : undefined}
                />
              </div>
            </section>
          </div>
        }
        inventory={
          <div className="flex flex-col gap-4">
            <AssetLibrary />
            <div className="grid grid-cols-1 gap-3 border-t border-workshop-border pt-3 md:grid-cols-2">
              <UploadDropzone />
              <CrawlerPanel />
            </div>
          </div>
        }
        logging={<LoggingPanel preferredConversationId={activeConversationId} />}
        workflow={<AgentWorkflowPanel />}
      />

      {showGenericEscalation && (
        <EscalationDialog escalation={showGenericEscalation} onDecision={cad.resolveEscalation} />
      )}
    </>
  );
}

export default App;
