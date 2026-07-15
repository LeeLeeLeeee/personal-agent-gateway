import { useId, useState } from "react";

const EFFORT_LABELS = { low: "LOW", medium: "MED", high: "HIGH", xhigh: "XHIGH", max: "MAX" };

const AGENT_INFO = "이 세션/페르소나가 사용할 CLI 백엔드입니다.";
const MODEL_INFO = "선택한 백엔드가 사용할 모델입니다.";
const OPTION_INFO = {
  effort: {
    label: "모델의 추론(사고) 노력 수준입니다. 높을수록 더 깊이 생각하지만 느리고 비쌉니다.",
    choices: {
      low: "가장 빠름, 얕은 추론",
      medium: "속도와 품질의 균형",
      high: "깊은 추론",
      xhigh: "매우 깊은 추론",
      max: "최대 추론(가장 느림)",
      ultra: "최대 추론(가장 느림)"
    }
  },
  sandbox: {
    label: "에이전트가 파일을 어디까지 쓸 수 있는지 범위입니다.",
    choices: {
      "read-only": "파일 수정 불가, 읽기만",
      "workspace-write": "워크스페이스 안에만 쓰기 허용",
      "danger-full-access": "시스템 전체 읽기/쓰기 (위험)"
    }
  },
  approval_policy: {
    label: "위험한 작업을 실행하기 전에 승인을 요청할지 정합니다.",
    choices: {
      never: "승인 없이 자동 실행",
      "on-request": "위험한 작업 전 사용자 확인",
      "on-failure": "실패했을 때만 확인",
      untrusted: "신뢰되지 않은 작업만 확인"
    }
  },
  permission_mode: {
    label: "도구 실행과 파일 편집을 어떻게 승인할지 정합니다.",
    choices: {
      default: "작업마다 수동 승인",
      manual: "작업마다 수동 승인",
      plan: "계획만 세우고 실행하지 않음",
      acceptEdits: "편집은 자동 승인",
      bypassPermissions: "모든 권한 자동 승인 (위험)"
    }
  }
};

function InfoTip({ label, children }) {
  const tipId = useId();
  return (
    <span className="config-info">
      <button type="button" className="config-info-btn" aria-label={`${label} 설명`} aria-describedby={tipId}>ⓘ</button>
      <span className="config-info-pop" role="tooltip" id={tipId}>{children}</span>
    </span>
  );
}

function modelOptions(agent, configuredModel) {
  const detected = agent.model_options?.length
    ? agent.model_options
    : (agent.models || []).map((id) => ({ id, label: id, efforts: [] }));
  if (!configuredModel || detected.some((model) => model.id === configuredModel)) return detected;
  return [{ id: configuredModel, label: configuredModel, efforts: [] }, ...detected];
}

function selectedModel(agent, modelId) {
  return modelOptions(agent, modelId).find((model) => model.id === modelId) || null;
}

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
    const nextOptions = { ...(config.options || {}) };
    const metadata = selectedModel(current, model);
    if (metadata?.efforts?.length && !metadata.efforts.includes(nextOptions.effort)) {
      nextOptions.effort = metadata.efforts.includes(metadata.default_effort)
        ? metadata.default_effort
        : metadata.efforts.includes(current.defaults?.effort)
          ? current.defaults.effort
          : metadata.efforts[0];
    }
    emit({ model, options: nextOptions });
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
    const info = OPTION_INFO[option.name];
    const metadata = selectedModel(current, config.model);
    const choices = option.name === "effort" && metadata?.efforts?.length
      ? metadata.efforts
      : (option.choices || []);

    if (option.name === "effort" && option.kind === "select") {
      return (
        <div className="config-field" key={option.name}>
          <span className="config-field-label mono">
            EFFORT
            <InfoTip label="effort">
              <span className="config-info-lead">{OPTION_INFO.effort.label}</span>
              <span className="config-info-list">
                {choices.map((choice) => (
                  <span className="config-info-list-row" key={choice}>
                    <b>{EFFORT_LABELS[choice] || choice.toUpperCase()}</b> {OPTION_INFO.effort.choices[choice] || ""}
                  </span>
                ))}
              </span>
            </InfoTip>
          </span>
          <div className="config-segment" role="group" aria-label="effort">
            {choices.map((choice) => (
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
        <span className="config-field-label mono">
          {label.toUpperCase()}
          {info ? <InfoTip label={label}>{info.label}</InfoTip> : null}
        </span>
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
              {choices.map((choice) => (
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
                  {info?.choices?.[choice] ? (
                    <span className="config-menu-sub mono">{info.choices[choice]}</span>
                  ) : null}
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
          <span className="config-field-label mono">AGENT<InfoTip label="agent">{AGENT_INFO}</InfoTip></span>
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
                        {disabled
                          ? (agent.availability_error || "unavailable")
                          : `${agent.default_model} · ${agent.version || "available"}`}
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
            <span className="config-field-label mono">MODEL<InfoTip label="model">{MODEL_INFO}</InfoTip></span>
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
                  {modelOptions(current, config.model).map((model) => (
                    <button
                      key={model.id}
                      type="button"
                      className={`config-menu-item${model.id === config.model ? " active" : ""}`}
                      onClick={() => changeModel(model.id)}
                    >
                      <span className="config-menu-item-row">
                        <span className="config-menu-mark mono">{model.id === config.model ? "✓" : ""}</span>
                        <span className="config-menu-name mono">{model.label || model.id}</span>
                      </span>
                      {model.description ? <span className="config-menu-sub mono">{model.description}</span> : null}
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
