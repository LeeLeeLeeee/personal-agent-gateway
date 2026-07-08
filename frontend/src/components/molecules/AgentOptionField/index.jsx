import { InputField } from "../../atoms/Field/index.jsx";

function labelFor(option) {
  return option.name.replaceAll("_", " ");
}

export function AgentOptionField({ option, value, disabled = false, onChange }) {
  const label = labelFor(option);

  if (option.kind === "select") {
    return (
      <label className="agent-field">
        <span className="agent-field-label mono">{label}</span>
        <InputField
          as="select"
          aria-label={label}
          value={value || ""}
          disabled={disabled}
          onChange={(event) => onChange(option.name, event.target.value)}
        >
          {(option.choices || []).map((choice) => (
            <option key={choice} value={choice}>{choice}</option>
          ))}
        </InputField>
      </label>
    );
  }

  return (
    <label className="agent-field">
      <span className="agent-field-label mono">{label}</span>
      <InputField
        aria-label={label}
        value={value || ""}
        disabled={disabled}
        onChange={(event) => onChange(option.name, event.target.value)}
      />
    </label>
  );
}
