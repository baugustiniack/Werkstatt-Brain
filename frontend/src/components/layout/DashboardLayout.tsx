import { useState, type ReactNode } from "react";

import { SettingsPanel } from "./SettingsPanel";

export type DashboardTab = "workspace" | "inventory" | "logging" | "workflow";

interface PanelProps {
  title: string;
  children: ReactNode;
  className?: string;
}

function Panel({ title, children, className = "" }: PanelProps) {
  return (
    <section className={`flex min-h-0 flex-col rounded-lg border border-workshop-border bg-workshop-panel ${className}`}>
      <header className="border-b border-workshop-border px-4 py-2">
        <h2 className="text-xs font-mono font-semibold tracking-widest text-workshop-muted uppercase">{title}</h2>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
    </section>
  );
}

interface DashboardLayoutProps {
  workspace: ReactNode;
  inventory: ReactNode;
  logging: ReactNode;
  workflow: ReactNode;
  activeTab: DashboardTab;
  onTabChange: (tab: DashboardTab) => void;
}

const TABS: { id: DashboardTab; label: string }[] = [
  { id: "workspace", label: "Conversation & Model Viewer" },
  { id: "inventory", label: "Inventory Database" },
  { id: "logging", label: "Logging" },
  { id: "workflow", label: "Agent Workflow" },
];

/** Dashboard mit Reitern (Workspace, Inventar, Logging, Agent Workflow). */
export function DashboardLayout({
  workspace,
  inventory,
  logging,
  workflow,
  activeTab,
  onTabChange,
}: DashboardLayoutProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="flex h-screen flex-col bg-workshop-bg text-workshop-text">
      <header className="flex flex-wrap items-center gap-3 border-b border-workshop-border px-4 py-3 sm:px-6">
        <span className="text-lg font-bold tracking-tight">Werkstatt-Brain</span>
        <nav className="flex min-w-0 flex-1 flex-wrap gap-1" aria-label="Hauptbereiche">
          {TABS.map((tab) => {
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => onTabChange(tab.id)}
                className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                  active
                    ? "bg-workshop-accent text-workshop-bg"
                    : "text-workshop-muted hover:bg-workshop-panel hover:text-workshop-text"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </nav>
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          title="Einstellungen: API-Keys hinterlegen"
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-workshop-border px-3 py-1.5 text-xs font-semibold text-workshop-text hover:border-workshop-accent hover:bg-workshop-panel"
        >
          <span aria-hidden>⚙</span>
          <span>Einstellungen / API-Key</span>
        </button>
      </header>
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
      <main className="min-h-0 flex-1 p-3">
        {activeTab === "workspace" && workspace}
        {activeTab === "inventory" && (
          <Panel title="Inventory &amp; Assets" className="h-full">
            {inventory}
          </Panel>
        )}
        {activeTab === "logging" && (
          <Panel title="Agent-Logs" className="h-full">
            {logging}
          </Panel>
        )}
        {activeTab === "workflow" && (
          <Panel title="Agent Workflow &amp; Meta-Coach" className="h-full">
            {workflow}
          </Panel>
        )}
      </main>
    </div>
  );
}
