import { InputField } from "../../atoms/Field/index.jsx";
import { AgentAvailabilityBadge } from "../../molecules/AgentAvailabilityBadge/index.jsx";
import { AgentOptionField } from "../../molecules/AgentOptionField/index.jsx";

function selectedAgent(agents, config) {
  const configured = agents.find((agent) => agent.id === config?.agent_id);

  if (configured) return configured;

  return agents.find((agent) => agent.available) || agents[0] || null;
}

function optionValue(config, agent, name) {
  return config.options?.[name] || agent.defaults?.[name] || "";
}

export function AgentPicker({ agents = [], config, onChange, error = "" }) {
  const current = selectedAgent(agents, config);

  if (!current || !config) return null;

  if (!config.editable) {
    const options = Object.entries(config.options || {})
      .map(([key, value]) => `${key}: ${value}`)
      .join(" / ");

    return (
      <section className="agent-picker agent-picker-locked" aria-label="Agent configuration">
        <div className="agent-picker-head">
          <span className="headline" style={{ fontSize: 12 }}>Agent</span>
          <AgentAvailabilityBadge available={current.available} reason={current.availability_error} />
        </div>
        <div className="agent-picker-summary mono">
          Locked - {current.label} / {config.model}{options ? ` / ${options}` : ""}
        </div>
      </section>
    );
  }

  function emit(next) {
    onChange({ ...config, ...next });
  }

  function changeAgent(agentId) {
    const agent = agents.find((candidate) => candidate.id === agentId);
    if (!agent || !agent.available) return;

    emit({
      agent_id: agent.id,
      model: agent.default_model,
      options: { ...(agent.defaults || {}) }
    });
  }

  function changeOption(name, value) {
    emit({ options: { ...(config.options || {}), [name]: value } });
  }

  return (
    <section className="agent-picker" aria-label="Agent configuration">
      <div className="agent-picker-head">
        <span className="headline" style={{ fontSize: 12 }}>Agent</span>
      </div>
      <div className="agent-list">
        {agents.map((agent) => (
          <button
            key={agent.id}
            type="button"
            className={`agent-choice${agent.id === current.id ? " active" : ""}`}
            disabled={!agent.available}
            aria-pressed={agent.id === current.id}
            onClick={() => changeAgent(agent.id)}
          >
            <div className="agent-choice-row">
              <span className="agent-choice-label">{agent.label}</span>
              <AgentAvailabilityBadge available={agent.available} reason={agent.availability_error} />
            </div>
            {!agent.available && agent.availability_error ? (
              <small className="agent-choice-meta mono">{agent.availability_error}</small>
            ) : null}
          </button>
        ))}
      </div>
      {current.available ? (
        <>
          <label className="agent-field">
            <span className="agent-field-label mono">Model</span>
            <InputField
              as="select"
              aria-label="Model"
              value={config.model}
              onChange={(event) => emit({ model: event.target.value })}
            >
              {(current.models || []).map((model) => (
                <option key={model} value={model}>{model}</option>
              ))}
            </InputField>
          </label>
          {(current.options_schema || []).map((option) => (
            <AgentOptionField
              key={option.name}
              option={option}
              value={optionValue(config, current, option.name)}
              onChange={changeOption}
            />
          ))}
        </>
      ) : null}
      {error ? <div className="agent-picker-error mono">{error}</div> : null}
    </section>
  );
}
