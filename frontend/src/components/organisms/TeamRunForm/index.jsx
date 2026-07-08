import { useState } from "react";
import { Button } from "../../atoms/Button/index.jsx";
import { InputField } from "../../atoms/Field/index.jsx";

const RUN_MODES = ["planning_only", "plan_and_execute", "review_only"];

export function TeamRunForm({ personas = [], onSubmit }) {
  const [goal, setGoal] = useState("");
  const [leaderPersonaId, setLeaderPersonaId] = useState("");
  const [memberPersonaIds, setMemberPersonaIds] = useState([]);
  const [runMode, setRunMode] = useState("planning_only");
  const [maxWorkers, setMaxWorkers] = useState(3);

  function toggleMember(personaId) {
    setMemberPersonaIds((prev) =>
      prev.includes(personaId) ? prev.filter((id) => id !== personaId) : [...prev, personaId]
    );
  }

  function submit(event) {
    event.preventDefault();
    onSubmit({
      goal: goal.trim(),
      leader_persona_id: leaderPersonaId,
      member_persona_ids: memberPersonaIds,
      run_mode: runMode,
      max_workers: Number(maxWorkers) || 1
    });
  }

  return (
    <form className="team-run-form" onSubmit={submit} aria-label="New team run">
      <label className="team-run-field">
        <span className="headline" style={{ fontSize: 13 }}>Goal</span>
        <InputField as="textarea" aria-label="Goal" value={goal} onChange={(event) => setGoal(event.target.value)} />
      </label>

      <label className="team-run-field">
        <span className="headline" style={{ fontSize: 13 }}>Leader</span>
        <InputField
          as="select"
          aria-label="Leader"
          value={leaderPersonaId}
          onChange={(event) => setLeaderPersonaId(event.target.value)}
        >
          <option value="">Select leader</option>
          {personas.map((persona) => (
            <option key={persona.id} value={persona.id}>{persona.name}</option>
          ))}
        </InputField>
      </label>

      <div className="team-run-field">
        <span className="headline" style={{ fontSize: 13 }}>Member personas</span>
        <div className="team-run-members">
          {personas.map((persona) => (
            <label key={persona.id} className="team-run-member">
              <input
                type="checkbox"
                checked={memberPersonaIds.includes(persona.id)}
                onChange={() => toggleMember(persona.id)}
              />
              {persona.name}
            </label>
          ))}
        </div>
      </div>

      <label className="team-run-field">
        <span className="headline" style={{ fontSize: 13 }}>Run mode</span>
        <InputField
          as="select"
          aria-label="Run mode"
          value={runMode}
          onChange={(event) => setRunMode(event.target.value)}
        >
          {RUN_MODES.map((mode) => (
            <option key={mode} value={mode}>{mode}</option>
          ))}
        </InputField>
      </label>

      <label className="team-run-field">
        <span className="headline" style={{ fontSize: 13 }}>Max workers</span>
        <InputField
          type="number"
          min="1"
          aria-label="Max workers"
          value={maxWorkers}
          onChange={(event) => setMaxWorkers(event.target.value)}
        />
      </label>

      <Button type="submit" variant="primary" size="btn-lg">Create Team Run</Button>
    </form>
  );
}
