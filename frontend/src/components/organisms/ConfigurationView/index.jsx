import "./ConfigurationView.css";

const SECTIONS = [
  ["teams", "Teams"],
  ["personas", "Personas"],
  ["policies", "Policies"],
  ["automations", "Automations"]
];

const POLICY_SECTIONS = [
  ["rules", "Instructions"],
  ["spaces", "Workspace access"]
];

const AUTOMATION_SECTIONS = [
  ["schedules", "Schedules"],
  ["hooks", "Email triggers"],
  ["jobs", "Run history"]
];

function Tabs({ label, items, value, onChange, secondary = false }) {
  return (
    <div className={`configuration-tabs${secondary ? " configuration-tabs-secondary" : ""}`} role="tablist" aria-label={label}>
      {items.map(([key, text]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          className={`configuration-tab${value === key ? " active" : ""}`}
          onClick={() => onChange(key)}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export function ConfigurationView({
  section,
  policySection,
  automationSection,
  onSectionChange,
  onPolicySectionChange,
  onAutomationSectionChange,
  children
}) {
  return (
    <section className="screen configuration-view">
      <div className="configuration-head">
        <h1 className="headline">Configuration</h1>
        <p>실행에 재사용되는 팀, 정책, 자동화 설정을 관리합니다.</p>
      </div>
      <Tabs label="Configuration sections" items={SECTIONS} value={section} onChange={onSectionChange} />
      {section === "policies" ? (
        <Tabs label="Policy sections" items={POLICY_SECTIONS} value={policySection} onChange={onPolicySectionChange} secondary />
      ) : null}
      {section === "automations" ? (
        <Tabs label="Automation sections" items={AUTOMATION_SECTIONS} value={automationSection} onChange={onAutomationSectionChange} secondary />
      ) : null}
      <div className="configuration-content">{children}</div>
    </section>
  );
}
