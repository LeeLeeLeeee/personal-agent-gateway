import { useEffect, useState } from "react";
import { api } from "../../../api/client.js";
import "./DashboardView.css";

const STATUS_LABELS = {
  ok: "수집 완료",
  partial: "일부 수집",
  unconfirmed: "미수집",
  unavailable: "실행 불가"
};

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function formatAmount(value) {
  return isNumber(value) ? value.toLocaleString("ko-KR") : "미수집";
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

function UsageGauge({ label, used, limit }) {
  if (!isNumber(used) || !isNumber(limit) || limit <= 0) return null;
  const percent = Math.min(Math.max((used / limit) * 100, 0), 100);

  return (
    <div className="dashboard-usage-gauge-wrap">
      <div
        className="dashboard-usage-gauge"
        role="progressbar"
        aria-label={`${label} 주간 사용량`}
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={Math.min(Math.max(used, 0), limit)}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="dashboard-usage-gauge-label mono">
        {formatAmount(used)} / {formatAmount(limit)} ({Math.round(percent)}%)
      </div>
    </div>
  );
}

function UsageMetric({ label, value }) {
  return (
    <div className="dashboard-usage-metric">
      <dt>{label}</dt>
      <dd className={isNumber(value) ? "mono" : "dashboard-usage-missing"}>
        {formatAmount(value)}
      </dd>
    </div>
  );
}

function ProviderUsageCard({ usage }) {
  const label = usage.label || usage.provider || "에이전트";
  const status = usage.available === false ? "unavailable" : usage.usage_status;
  const statusLabel = STATUS_LABELS[status] || "확인 필요";
  const hasGauge = isNumber(usage.used) && isNumber(usage.weekly_limit) && usage.weekly_limit > 0;

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
          {hasGauge ? (
            <UsageGauge label={label} used={usage.used} limit={usage.weekly_limit} />
          ) : (
            <div className="dashboard-usage-empty">
              사용량 데이터가 아직 수집되지 않았습니다.
            </div>
          )}
          <dl className="dashboard-usage-metrics">
            <UsageMetric label="주간 한도" value={usage.weekly_limit} />
            <UsageMetric label="이번 주 사용량" value={usage.used} />
            <UsageMetric label="남은 한도" value={usage.remaining} />
            <div className="dashboard-usage-metric">
              <dt>초기화 시각</dt>
              <dd className={usage.reset_at ? "mono" : "dashboard-usage-missing"}>
                {formatDateTime(usage.reset_at)}
              </dd>
            </div>
          </dl>
          {usage.note ? <p className="dashboard-usage-note">{usage.note}</p> : null}
        </>
      )}
    </article>
  );
}

export function DashboardView() {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

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

  const providers = report?.providers || [];

  return (
    <section className="screen dashboard-view">
      <div className="dashboard-head">
        <div>
          <h1 className="headline">대시보드</h1>
          <p>로컬 에이전트의 주간 사용량을 한눈에 확인합니다.</p>
        </div>
        {report?.detected_at ? (
          <div className="dashboard-detected-at mono">
            마지막 확인 · {formatDateTime(report.detected_at)}
          </div>
        ) : null}
      </div>

      <section className="dashboard-usage-section" aria-labelledby="dashboard-usage-title">
        <div className="dashboard-section-head">
          <div>
            <h2 id="dashboard-usage-title" className="headline">이번 주 사용량</h2>
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

        {loading ? <div className="dashboard-state" role="status">사용량을 불러오는 중입니다.</div> : null}
        {!loading && error ? (
          <div className="dashboard-state dashboard-state-error" role="alert">
            <strong>사용량을 불러오지 못했습니다.</strong>
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
          <div className="dashboard-state">표시할 로컬 에이전트가 없습니다.</div>
        ) : null}
        {!loading && !error && providers.length > 0 ? (
          <div className="dashboard-usage-grid">
            {providers.map((usage) => (
              <ProviderUsageCard key={usage.provider} usage={usage} />
            ))}
          </div>
        ) : null}
      </section>
    </section>
  );
}
