import { useEffect, useState } from "react";
import { api, apiErrorAction } from "../../../api/client.js";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import "./DashboardView.css";
import { isOperationsPayload, operationsDashboardModel } from "./operationsModel.js";

const STATUS_LABELS = {
  ok: "수집 완료",
  partial: "일부 수집",
  unconfirmed: "미수집",
  unavailable: "실행 불가"
};

const DEFAULT_VISIBLE_SESSION_COUNT = 5;

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function formatDateTime(value) {
  if (!value) return "미수집";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

function rateLimitWindowLabel(windowMinutes) {
  if (windowMinutes === 300) return "5시간";
  if (windowMinutes === 10080) return "7일";
  return isNumber(windowMinutes) ? `${windowMinutes}분` : "기간 미확인";
}

function RateLimitGauge({ providerLabel, rateLimit }) {
  if (!isNumber(rateLimit?.used_percent)) return null;
  const percent = Math.min(Math.max(rateLimit.used_percent, 0), 100);
  // A per-model scoped window and the account-wide window of the same length
  // are otherwise indistinguishable — same duration, often the same reset.
  const windowLabel = rateLimit.scope
    ? `${rateLimitWindowLabel(rateLimit.window_minutes)} · ${rateLimit.scope}`
    : rateLimitWindowLabel(rateLimit.window_minutes);

  return (
    <div className="dashboard-usage-gauge-wrap">
      <div
        className="dashboard-usage-gauge"
        role="progressbar"
        aria-label={`${providerLabel} ${windowLabel} 한도`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="dashboard-usage-gauge-label mono">
        {windowLabel} · {Math.round(percent)}%
        {rateLimit.resets_at ? ` · 초기화 ${formatDateTime(rateLimit.resets_at)}` : ""}
      </div>
    </div>
  );
}

function ProviderUsageCard({ usage }) {
  const label = usage.label || usage.provider || "에이전트";
  const status = usage.available === false ? "unavailable" : usage.usage_status;
  const statusLabel = STATUS_LABELS[status] || "확인 필요";
  const rateLimits = Array.isArray(usage.rate_limits) ? usage.rate_limits : [];
  const collectedRateLimits = rateLimits.filter((rateLimit) => isNumber(rateLimit?.used_percent));

  return (
    <article className="dashboard-usage-card" aria-labelledby={`usage-${usage.provider}`}>
      <div className="dashboard-usage-card-head">
        <div>
          <h3 id={`usage-${usage.provider}`} className="headline">{label}</h3>
          <div className="dashboard-usage-provider-meta mono">
            {usage.version ? `버전 ${usage.version}` : "버전 미확인"}
            {usage.model ? ` · 모델 ${usage.model}` : ""}
          </div>
        </div>
        <span className={`dashboard-usage-status dashboard-usage-status-${status}`}>
          {statusLabel}
        </span>
      </div>

      {usage.available === false ? (
        <div className="dashboard-usage-unavailable" role="status">
          이 에이전트는 현재 실행할 수 없습니다.
          {usage.availability_error ? <span>{usage.availability_error}</span> : null}
        </div>
      ) : (
        <>
          <p className="dashboard-usage-note">계정 전체 한도</p>
          {collectedRateLimits.length ? (
            collectedRateLimits.map((rateLimit) => (
              <RateLimitGauge
                key={`${rateLimit.window_minutes}-${rateLimit.scope || ""}-${rateLimit.resets_at || ""}`}
                providerLabel={label}
                rateLimit={rateLimit}
              />
            ))
          ) : (
            <div className="dashboard-usage-empty">
              계정 한도를 수집하지 못했습니다.
            </div>
          )}
          {usage.note ? <p className="dashboard-usage-note">{usage.note}</p> : null}
        </>
      )}
    </article>
  );
}

function formatBytes(value) {
  if (!isNumber(value) || value < 0) return "미확인";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB"];
  let n = value / 1024, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(1)} ${units[i]}`;
}

function errorMessage(error) {
  return typeof error?.detail === "string" ? error.detail : error?.message || "잠시 후 다시 시도해 주세요.";
}

function OperationRows({ items, emptyMessage, onOpenTarget, attention = false }) {
  if (!items.length) {
    return <div className="dashboard-operation-empty">{emptyMessage}</div>;
  }

  return (
    <div className={`dashboard-operation-list${attention ? " dashboard-operation-list-attention" : ""}`}>
      {items.map((item) => (
        <article className="dashboard-operation-row" key={`${item.domain}:${item.id}`}>
          <div className="dashboard-operation-row-main">
            <h3>{item.title || "제목 없는 작업"}</h3>
            <p className="mono">
              {String(item.domain || "operation").replaceAll("_", " ").toUpperCase()}
              {item.updated_at ? ` · ${formatDateTime(item.updated_at)}` : " · 갱신 시각 미확인"}
            </p>
            {item.pause_reason ? <p className="dashboard-operation-reason">{item.pause_reason}</p> : null}
          </div>
          <StatusBadge kind={item.status || "idle"} />
          {item.target && onOpenTarget ? (
            <button
              type="button"
              className="btn btn-sm"
              aria-label={`${item.title || "작업"} 상세 열기`}
              onClick={() => onOpenTarget(item.target)}
            >
              열기
            </button>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function SystemStatus({ model, operations }) {
  const accessMode = operations.access_mode ? String(operations.access_mode).toUpperCase() : "미확인";
  const workspace = operations.diagnostics.workspace_writable;

  return (
    <>
      <div className="dashboard-system-summary mono">
        INTAKE · {operations.intake_open ? "OPEN" : "STOPPED"}
        <span>ACCESS · {accessMode}</span>
        <span>WORKSPACE · {workspace === true ? "WRITABLE" : workspace === false ? "BLOCKED" : "UNCONFIRMED"}</span>
      </div>
      {model.health.length ? (
        <div className="dashboard-health-grid">
          {model.health.map((component) => (
            <article className="dashboard-health-card" key={component.name || component.detail}>
              <div>
                <h3 className="mono">{component.name || "unknown"}</h3>
                <p>{typeof component.detail === "string" ? component.detail : "상태 정보가 없습니다."}</p>
              </div>
              <StatusBadge kind={component.ready === true ? "completed" : "failed"} />
            </article>
          ))}
        </div>
      ) : (
        <div className="dashboard-operation-empty">시스템 상태 정보가 없습니다.</div>
      )}
    </>
  );
}

function OperationsDashboard({
  data, error, loading, onReload, onOpenTarget, onRelogin, onStartChat, onStartTeamRun
}) {
  const model = data ? operationsDashboardModel(data) : null;
  const attentionCount = model ? model.attentionItems.length + model.systemAttention.length : 0;
  const errorAction = apiErrorAction(error);

  return (
    <section className="dashboard-operations-section" aria-labelledby="dashboard-operations-title">
      <div className="dashboard-section-head">
        <div>
          <h2 id="dashboard-operations-title" className="headline">운영 현황</h2>
          <p>진행 중인 작업과 조치가 필요한 상태를 확인합니다.</p>
        </div>
        {!loading ? (
          <button type="button" className="btn btn-sm" onClick={onReload}>새로고침</button>
        ) : null}
      </div>

      {loading && !data ? <div className="dashboard-state" role="status">운영 현황을 불러오는 중입니다.</div> : null}
      {error ? (
        <div className="dashboard-state dashboard-state-error" role="alert">
          <strong>운영 현황을 불러오지 못했습니다.</strong>
          <span>{errorMessage(error)}</span>
          {data ? <span>마지막으로 성공한 정보를 계속 표시합니다.</span> : null}
          <button
            type="button"
            className="btn btn-sm"
            onClick={errorAction === "relogin" && onRelogin ? onRelogin : onReload}
          >
            {errorAction === "relogin" && onRelogin ? "다시 로그인" : "다시 시도"}
          </button>
        </div>
      ) : null}

      {model ? (
        <>
          <section className="dashboard-operation-panel dashboard-attention-panel" aria-labelledby="dashboard-attention-title">
            <h3 id="dashboard-attention-title" className="headline">조치 필요</h3>
            {model.systemAttention.map((item) => (
              <article className="dashboard-operation-row dashboard-operation-system-alert" key={item.id}>
                <div className="dashboard-operation-row-main">
                  <h4>{item.title}</h4>
                  <p>{item.detail}</p>
                </div>
                <StatusBadge kind={item.kind} />
              </article>
            ))}
            <OperationRows
              items={model.attentionItems.slice(0, 5)}
              emptyMessage={model.systemAttention.length ? "작업 항목에는 추가 조치가 필요하지 않습니다." : "조치가 필요한 항목이 없습니다."}
              onOpenTarget={onOpenTarget}
              attention
            />
          </section>

          <div className="dashboard-operations-summary" aria-label="운영 요약">
            <div className="dashboard-summary-card">
              <span>진행 중</span>
              <strong className="mono">{model.activeItems.length}</strong>
            </div>
            <div className={`dashboard-summary-card${attentionCount ? " dashboard-summary-card-danger" : ""}`}>
              <span>조치 필요</span>
              <strong className="mono">{attentionCount}</strong>
            </div>
            <div className="dashboard-summary-card">
              <span>정상 시스템</span>
              <strong className="mono">{model.healthyCount} / {model.health.length}</strong>
            </div>
          </div>

          <div className="dashboard-operations-grid">
            <section className="dashboard-operation-panel" aria-labelledby="dashboard-active-title">
              <h3 id="dashboard-active-title" className="headline">진행 중</h3>
              <OperationRows
                items={model.activeItems.slice(0, 5)}
                emptyMessage="현재 진행 중인 작업이 없습니다."
                onOpenTarget={onOpenTarget}
              />
            </section>

            <section className="dashboard-operation-panel" aria-labelledby="dashboard-results-title">
              <h3 id="dashboard-results-title" className="headline">최근 결과</h3>
              <OperationRows
                items={model.recentItems.slice(0, 5)}
                emptyMessage="최근 완료된 결과가 없습니다."
                onOpenTarget={onOpenTarget}
              />
            </section>
          </div>
        </>
      ) : null}

      <section className="dashboard-start-section" aria-labelledby="dashboard-start-title">
        <div>
          <h2 id="dashboard-start-title" className="headline">작업 시작</h2>
          <p>목표에 맞는 실행 방식을 선택하세요.</p>
        </div>
        <div className="dashboard-start-actions">
          <button type="button" className="btn btn-primary" onClick={onStartChat}>Chat 시작</button>
          <button type="button" className="btn" onClick={onStartTeamRun}>Team Run 시작</button>
        </div>
      </section>

      {model ? (
        <section className="dashboard-operation-panel dashboard-system-panel" aria-labelledby="dashboard-system-title">
          <h3 id="dashboard-system-title" className="headline">시스템 요약</h3>
          <SystemStatus model={model} operations={data} />
        </section>
      ) : null}
    </section>
  );
}

export function DashboardView({ onOpenTarget, onRelogin, onStartChat, onStartTeamRun }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [operations, setOperations] = useState(null);
  const [operationsError, setOperationsError] = useState(null);
  const [operationsLoading, setOperationsLoading] = useState(true);
  const [operationsReloadKey, setOperationsReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    api.dashboardUsage()
      .then((nextReport) => {
        if (active) setReport(nextReport);
      })
      .catch((nextError) => {
        if (active) setError(nextError);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [reloadKey]);

  useEffect(() => {
    let active = true;
    setOperationsLoading(true);
    setOperationsError(null);

    api.operations()
      .then((nextOperations) => {
        if (!isOperationsPayload(nextOperations)) {
          throw new Error("운영 현황 응답에 필요한 정보가 없습니다.");
        }
        if (active) setOperations(nextOperations);
      })
      .catch((nextError) => {
        if (active) setOperationsError(nextError);
      })
      .finally(() => {
        if (active) setOperationsLoading(false);
      });

    return () => {
      active = false;
    };
  }, [operationsReloadKey]);

  const [sessions, setSessions] = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState(null);
  const [lmgStatus, setLmgStatus] = useState({ status: "ready", message: null });
  const [sessionsReloadKey, setSessionsReloadKey] = useState(0);
  const [showAllSessions, setShowAllSessions] = useState(false);

  useEffect(() => {
    let active = true;
    setSessionsLoading(true);
    setSessionsError(null);
    api.dashboardSessions()
      .then((data) => {
        if (!active) return;
        const nextLmgStatus = data?.lmg;
        if (!nextLmgStatus || typeof nextLmgStatus.status !== "string") {
          setSessions([]);
          setLmgStatus({
            status: "protocol_error",
            message: "로컬 모델 게이트웨이 응답 형식이 올바르지 않습니다."
          });
          return;
        }
        setSessions(Array.isArray(data?.sessions) ? data.sessions : []);
        setLmgStatus(nextLmgStatus);
      })
      .catch((err) => {
        if (!active) return;
        setSessions([]);
        setSessionsError(err);
      })
      .finally(() => { if (active) setSessionsLoading(false); });
    return () => { active = false; };
  }, [sessionsReloadKey]);

  const providers = report?.providers || [];
  const visibleSessions = showAllSessions
    ? sessions
    : sessions.slice(0, DEFAULT_VISIBLE_SESSION_COUNT);
  const hiddenSessionCount = sessions.length - visibleSessions.length;

  return (
    <section className="screen dashboard-view">
      <div className="dashboard-head">
        <div>
          <h1 className="headline">Home</h1>
          <p>시작할 작업과 조치가 필요한 실행을 한곳에서 확인합니다.</p>
        </div>
        {report?.detected_at ? (
          <div className="dashboard-detected-at mono">
            마지막 확인 · {formatDateTime(report.detected_at)}
          </div>
        ) : null}
      </div>

      <OperationsDashboard
        data={operations}
        error={operationsError}
        loading={operationsLoading}
        onReload={() => setOperationsReloadKey((value) => value + 1)}
        onOpenTarget={onOpenTarget}
        onRelogin={onRelogin}
        onStartChat={onStartChat}
        onStartTeamRun={onStartTeamRun}
      />

      <section className="dashboard-usage-section" aria-labelledby="dashboard-usage-title">
        <div className="dashboard-section-head">
          <div>
            <h2 id="dashboard-usage-title" className="headline">계정 한도</h2>
            <p>Codex와 Claude의 확인 가능한 한도만 표시합니다.</p>
          </div>
          {!loading && (report || error) ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setReloadKey((value) => value + 1)}
            >
              새로고침
            </button>
          ) : null}
        </div>

        {loading ? <div className="dashboard-state" role="status">계정 한도를 불러오는 중입니다.</div> : null}
        {!loading && error ? (
          <div className="dashboard-state dashboard-state-error" role="alert">
            <strong>계정 한도를 불러오지 못했습니다.</strong>
            <span>{typeof error.detail === "string" ? error.detail : "잠시 후 다시 시도해 주세요."}</span>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setReloadKey((value) => value + 1)}
            >
              다시 시도
            </button>
          </div>
        ) : null}
        {!loading && !error && providers.length === 0 ? (
          <div className="dashboard-state">표시할 계정 한도 제공자가 없습니다.</div>
        ) : null}
        {!loading && !error && providers.length > 0 ? (
          <div className="dashboard-usage-grid">
            {providers.map((usage) => (
              <ProviderUsageCard key={usage.provider} usage={usage} />
            ))}
          </div>
        ) : null}
      </section>

      <section className="dashboard-usage-section" aria-labelledby="dashboard-sessions-title">
        <div className="dashboard-section-head">
          <div>
            <h2 id="dashboard-sessions-title" className="headline">로컬 세션</h2>
            <p>로컬 모델 게이트웨이가 관리하는 업스트림 세션입니다.</p>
          </div>
          {!sessionsLoading ? (
            <button type="button" className="btn btn-sm" onClick={() => setSessionsReloadKey((v) => v + 1)}>새로고침</button>
          ) : null}
        </div>
        {sessionsLoading ? <div className="dashboard-state" role="status">세션을 불러오는 중입니다.</div> : null}
        {!sessionsLoading && sessionsError ? (
          <div className="dashboard-state dashboard-state-error" role="alert">
            <strong>로컬 모델 게이트웨이에 연결할 수 없습니다.</strong>
            <button type="button" className="btn btn-sm" onClick={() => setSessionsReloadKey((v) => v + 1)}>다시 시도</button>
          </div>
        ) : null}
        {!sessionsLoading && !sessionsError && lmgStatus.status !== "ready" ? (
          <div className="dashboard-state dashboard-state-error" role="alert">
            <strong>{lmgStatus.message || "로컬 모델 게이트웨이 상태를 확인할 수 없습니다."}</strong>
            <button type="button" className="btn btn-sm" onClick={() => setSessionsReloadKey((v) => v + 1)}>다시 시도</button>
          </div>
        ) : null}
        {!sessionsLoading && !sessionsError && lmgStatus.status === "ready" && sessions.length === 0 ? (
          <div className="dashboard-state">로컬 세션 없음</div>
        ) : null}
        {!sessionsLoading && !sessionsError && lmgStatus.status === "ready" && sessions.length > 0 ? (
          <>
            <table className="dashboard-sessions-table">
              <thead>
                <tr><th>Provider</th><th>Model</th><th>용량</th><th>마지막 실행</th><th>생성</th><th>Workspace</th><th>세션 로그</th></tr>
              </thead>
              <tbody>
                {visibleSessions.map((s) => (
                  <tr key={s.upstream_id}>
                    <td>{s.provider}</td>
                    <td className="mono">{s.model}</td>
                    <td className="mono">{formatBytes(s.size_bytes)}</td>
                    <td>{formatDateTime(s.last_run_at)}</td>
                    <td>{formatDateTime(s.created_at)}</td>
                    <td className="mono dashboard-sessions-path">{s.workspace_root || "미확인"}</td>
                    <td className="mono dashboard-sessions-path">{s.storage_path || "미확인"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {hiddenSessionCount > 0 ? (
              <button
                type="button"
                className="btn btn-sm dashboard-sessions-more"
                onClick={() => setShowAllSessions(true)}
              >
                더보기 ({hiddenSessionCount})
              </button>
            ) : null}
          </>
        ) : null}
      </section>

    </section>
  );
}
