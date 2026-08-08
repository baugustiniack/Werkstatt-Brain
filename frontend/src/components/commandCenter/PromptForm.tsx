import type { FormEvent } from "react";

interface PromptFormProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
}

export function PromptForm({ value, onChange, onSubmit, disabled }: PromptFormProps) {
  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!disabled && value.trim().length >= 3) {
      onSubmit();
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2">
      <label htmlFor="prompt" className="text-xs font-semibold text-workshop-muted">
        Bauteilwunsch
      </label>
      <textarea
        id="prompt"
        rows={4}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder='z. B. "Erstelle eine Halterung für meine T-Nut-Schiene mit 8mm Senkkopfbohrung"'
        className="resize-none rounded-md border border-workshop-border bg-workshop-bg p-3 text-sm text-workshop-text placeholder:text-workshop-muted focus:border-workshop-accent focus:outline-none"
        disabled={disabled}
      />
      <button
        type="submit"
        disabled={disabled || value.trim().length < 3}
        className="self-end rounded-md bg-workshop-accent px-4 py-2 text-sm font-semibold text-workshop-bg transition disabled:cursor-not-allowed disabled:opacity-40"
      >
        {disabled ? "Läuft..." : "Bauteil generieren"}
      </button>
    </form>
  );
}
