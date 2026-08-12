// Every verification check kind is a file read -- file_nonempty, file_contains,
// file_matches. Nothing compiles, runs a test, or executes a command. So
// "verified" means the gate read the file and looked at its text, and labelling
// it 검증됨 would tell the operator something untrue.
const MODE_LABEL = { verified: "파일 내용 확인", attested: "워커 신고" };

export function BuildEvidence({ evidence }) {
  if (!evidence) return null;
  const {
    promised = [],
    declared = [],
    undeclared_promises: undeclared = [],
    extra_declarations: extra = [],
    missing_files: missing = [],
    verifications = []
  } = evidence;
  if (!promised.length && !declared.length && !verifications.length) return null;

  return (
    <div>
      <div className="mono team-task-dialog-label">약속한 파일 · 만든 파일</div>
      <div className="team-task-diagnostic mono">
        <div>{`약속 ${promised.length} · 신고 ${declared.length}`}</div>
        {undeclared.length ? <div>{`신고 안 된 약속: ${undeclared.join(", ")}`}</div> : null}
        {extra.length ? <div>{`계약 밖 신고: ${extra.join(", ")}`}</div> : null}
        {missing.length ? <div>{`신고했으나 없는 파일: ${missing.join(", ")}`}</div> : null}
        {verifications.map((item) => (
          <div key={item.name}>
            {`${item.name} · ${MODE_LABEL[item.mode] || item.mode} · ${String(item.status || "").toUpperCase()}`}
          </div>
        ))}
      </div>
    </div>
  );
}

export function BuildEvidenceSummary({ summary }) {
  if (!summary) return null;
  return (
    <span className="mono">
      {`워커 신고만으로 통과 ${summary.worker_asserted_only_count} / ${summary.task_count} · 없는 파일 ${summary.missing_file_count}`}
    </span>
  );
}
