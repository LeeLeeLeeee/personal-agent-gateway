import { useState } from "react";

const EFFORT_LABELS = { low: "LOW", medium: "MED", high: "HIGH", xhigh: "XHIGH", max: "MAX" };

function selectedAgent(agents, config) {
  const configured = agents.find((agent) => agent.id === config?.agent_id);
  if (configured) return configured;
  return agents.find((agent) => agent.available) || agents[0] || null;
}

function optionValue(config, agent, name) {
  return config.options?.[name] || agent.defaults?.[name] || "";
}

function optionLabel(name) {
  return name.replaceAll("_", " ");
}

export function AgentPicker({ agents = [], config, onChange, error = "", onRetry }) {
  const [menu, setMenu] = useState(null);
  const current = selectedAgent(agents, config);

  if (!current || !config) return null;

  // ---- LOCKED: read-only summary ----
  if (!config.editable) {
    const chips = [
      { k: "AGENT", v: current.label },
      { k: "MODEL", v: config.model },
      ...Object.entries(config.options || {})
        .filter(([, value]) => value)
        .map(([key, value]) => ({ k: key.toUpperCase(), v: String(value) }))
    ];
    return (
      <div className="config-bar config-bar-locked" aria-label="Session config">
        <span className="config-bar-locked-k mono">SESSION CONFIG</span>
        {chips.map((chip) => (
          <span className="config-chip" key={chip.k}>
            <span className="config-chip-k mono">{chip.k}</span>
            <span className="config-chip-v mono">{chip.v}</span>
          </span>
        ))}
        <span className="config-bar-locked-note mono">LOCKED · FIRST MESSAGE SENT</span>
      </div>
    );
  }

  // ---- EDITABLE ----
  function emit(next) {
    onChange({ ...config, ...next });
  }

  function changeAgent(agentId) {
    const agent = agents.find((candidate) => candidate.id === agentId);
    setMenu(null);
    if (!agent || !agent.available) return;
    emit({ agent_id: agent.id, model: agent.default_model, options: { ...(agent.defaults || {}) } });
  }

  function changeModel(model) {
    setMenu(null);
    emit({ model });
  }

  function changeOption(name, value) {
    emit({ options: { ...(config.options || {}), [name]: value } });
  }

  function toggle(key) {
    setMenu((cur) => (cur === key ? null : key));
  }

  function renderOption(option) {
    // Free-text options (codex profile, claude agent) are intentionally not exposed in the bar.
    if (option.kind !== "select") return null;

    const value = optionValue(config, current, option.name);
    const label = optionLabel(option.name);

    if (option.name === "effort" && option.kind === "select") {
      return (
        <div className="config-field" key={option.name}>
          <span className="config-field-label mono">EFFORT</span>
          <div className="config-segment" role="group" aria-label="effort">
            {(option.choices || []).map((choice) => (
              <button
                key={choice}
                type="button"
                aria-label={`effort ${choice}`}
                aria-pressed={value === choice}
                className={`config-seg-btn${value === choice ? " active" : ""}`}
                onClick={() => changeOption("effort", choice)}
              >
                {EFFORT_LABELS[choice] || choice.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      );
    }

    return (
      <div className="config-field" key={option.name}>
        <span className="config-field-label mono">{label.toUpperCase()}</span>
        <div className="config-dropdown">
          <button
            type="button"
            aria-label={label}
            aria-haspopup="listbox"
            aria-expanded={menu === option.name}
            className={`config-sel${menu === option.name ? " open" : ""}`}
            onClick={() => toggle(option.name)}
          >
            {value || "—"}<span className="config-sel-caret">▾</span>
          </button>
          {menu === option.name ? (
            <div className="config-menu" role="listbox" aria-label={`Select ${label}`}>
              {(option.choices || []).map((choice) => (
                <button
                  key={choice}
                  type="button"
                  className={`config-menu-item${choice === value ? " active" : ""}`}
                  onClick={() => { changeOption(option.name, choice); setMenu(null); }}
                >
                  <span className="config-menu-item-row">
                    <span className="config-menu-mark mono">{choice === value ? "✓" : ""}</span>
                    <span className="config-menu-name mono">{choice}</span>
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="config-bar" aria-label="Session config">
      {error ? (
        <div className="config-bar-error">
          <span className="config-bar-error-k mono">CONFIG UPDATE FAILED</span>
          <span className="config-bar-error-msg mono">{error}</span>
          {onRetry ? <button type="button" className="config-bar-retry" onClick={onRetry}>RETRY</button> : null}
        </div>
      ) : null}
      <div className="config-bar-row">
        {/* AGENT */}
        <div className="config-field">
          <span className="config-field-label mono">AGENT</span>
          <div className="config-dropdown">
            <button
              type="button"
              aria-label="Agent"
              aria-haspopup="listbox"
              aria-expanded={menu === "agent"}
              className={`config-sel${menu === "agent" ? " open" : ""}`}
              onClick={() => toggle("agent")}
            >
              {current.label}<span className="config-sel-caret">▾</span>
            </button>
            {menu === "agent" ? (
              <div className="config-menu" role="listbox" aria-label="Select agent">
                <div className="config-menu-head mono">SELECT AGENT</div>
                {agents.map((agent) => {
                  const active = agent.id === current.id;
                  const disabled = !agent.available;
                  return (
                    <button
                      key={agent.id}
                      type="button"
                      disabled={disabled}
                      aria-pressed={active}
                      className={`config-menu-item${active ? " active" : ""}${disabled ? " disabled" : ""}`}
                      onClick={() => changeAgent(agent.id)}
                    >
                      <span className="config-menu-item-row">
                        <span className="config-menu-mark mono">{active ? "✓" : (disabled ? "×" : "")}</span>
                        <span className="config-menu-name mono">{agent.label}</span>
                      </span>
                      <span className={`config-menu-sub mono${disabled ? " danger" : ""}`}>
                        {disabled ? (agent.availability_error || "unavailable") : `${agent.default_model} · available`}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
        </div>

        {/* MODEL */}
        {current.available ? (
          <div className="config-field">
            <span className="config-field-label mono">MODEL</span>
            <div className="config-dropdown">
              <button
                type="button"
                aria-label="Model"
                aria-haspopup="listbox"
                aria-expanded={menu === "model"}
                className={`config-sel${menu === "model" ? " open" : ""}`}
                onClick={() => toggle("model")}
              >
                {config.model}<span className="config-sel-caret">▾</span>
              </button>
              {menu === "model" ? (
                <div className="config-menu" role="listbox" aria-label="Select model">
                  {(current.models || []).map((model) => (
                    <button
                      key={model}
                      type="button"
                      className={`config-menu-item${model === config.model ? " active" : ""}`}
                      onClick={() => changeModel(model)}
                    >
                      <span className="config-menu-item-row">
                        <span className="config-menu-mark mono">{model === config.model ? "✓" : ""}</span>
                        <span className="config-menu-name mono">{model}</span>
                      </span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        ) : (
          <span className="config-unavailable mono">
            {current.availability_error || "UNAVAILABLE"} — pick an available agent
          </span>
        )}

        {/* OPTIONS from schema */}
        {current.available ? (current.options_schema || []).map(renderOption) : null}
      </div>
    </div>
  );
}
