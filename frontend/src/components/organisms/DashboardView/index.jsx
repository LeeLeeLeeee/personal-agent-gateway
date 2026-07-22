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

function OperationsDashboard({ data, error, loading, onReload, onOpenTarget, onRelogin }) {
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
              <h3 id="dashboard-active-title" className="headline">진행 중 작업</h3>
              <OperationRows
                items={model.activeItems.slice(0, 5)}
                emptyMessage="현재 진행 중인 작업이 없습니다."
                onOpenTarget={onOpenTarget}
              />
            </section>

            <section className="dashboard-operation-panel" aria-labelledby="dashboard-system-title">
              <h3 id="dashboard-system-title" className="headline">시스템 상태</h3>
              <SystemStatus model={model} operations={data} />
            </section>
          </div>

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
        </>
      ) : null}
    </section>
  );
}

export function DashboardView({ onOpenTarget, onRelogin }) {
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

      <OperationsDashboard
        data={operations}
        error={operationsError}
        loading={operationsLoading}
        onReload={() => setOperationsReloadKey((value) => value + 1)}
        onOpenTarget={onOpenTarget}
        onRelogin={onRelogin}
      />
    </section>
  );
}
