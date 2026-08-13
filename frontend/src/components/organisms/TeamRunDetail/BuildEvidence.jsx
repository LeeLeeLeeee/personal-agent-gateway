// Every verification check kind is a file read -- file_nonempty, file_contains,
// file_matches. Nothing compiles, runs a test, or executes a command. So
// "verified" means the gate read the file and looked at its text, and labelling
// it 검증됨 would tell the operator something untrue.
const MODE_LABEL = { verified: "파일 내용 확인", attested: "워커 신고", unverified: "미확인" };

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

// "워커 신고만으로 통과 0 / 13" reads as "acceptance was rigorous", which is the
// one thing this number cannot say. Every check kind in the system is a file
// read, so a task outside that count passed a check no stronger than "the file
// exists and has some text in it". The count is honest; only the wording can be
// fixed without a stronger check kind, so the label says what the checks are.
// The count is scoped by naming it, not by a separate flag: the rollup always
// covers exactly the task window /detail returned, so "태스크 13개 중" is true
// whether or not the run has more tasks above the limit.
//
// The second count says 게이트 미검사, not 워커 신고만, because attested_only is
// verified_count == 0 -- "the gate ran no check" -- and that is not the same as
// "the worker vouched for it". A task whose only required verification came back
// checked: false lands in this count while the worker vouched for nothing; it
// explicitly declined to. Naming the count after what the gate did keeps it true
// in both cases. It also overlaps 미확인 by design: the three numbers describe
// different things about the same tasks, not disjoint buckets.
export function BuildEvidenceSummary({ summary }) {
  if (!summary) return null;
  const inspected = summary.task_count - summary.worker_asserted_only_count;
  return (
    <span className="mono">
      {`태스크 ${summary.task_count}개 중 게이트가 파일 확인 ${inspected}`}
      {` · 게이트 미검사 ${summary.worker_asserted_only_count}`}
      {` (검사는 모두 파일 읽기 — 빌드·테스트 실행 없음)`}
      {` · 없는 파일 ${summary.missing_file_count}`}
      {` · 미확인 ${summary.unverified_task_count ?? 0}`}
    </span>
  );
}
