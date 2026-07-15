import { useState } from "react";
import { apiErrorAction } from "../../../api/client.js";
import { fmtDateTime } from "../../../lib/time.js";
import { StatusBadge } from "../../atoms/StatusBadge/index.jsx";
import { useConfirm } from "../../providers/UiProvider/index.jsx";

function ErrorPanel({ error, onRefresh, onRelogin }) {
  if (!error) return null;
  const action = apiErrorAction(error);
  const actionLabel = {
    relogin: "Sign in again",
    refresh: "Refresh state",
    retry: "Retry request"
  }[action];
  const handleAction = action === "relogin" ? onRelogin : onRefresh;
  return (
    <div className="operations-error" role="alert">
      <div className="operations-error-code mono">{error.code || "request_failed"}</div>
      <div>{typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail)}</div>
      <div className="operations-error-preservation">Existing local data was not cleared.</div>
      {error.correlationId ? (
        <div className="operations-correlation mono">
          CORRELATION · {error.correlationId}
          <button
            type="button"
            className="btn btn-sm"
            aria-label="Copy correlation ID"
            onClick={() => navigator.clipboard?.writeText(error.correlationId)}
          >
            Copy
          </button>
        </div>
      ) : null}
      {actionLabel && handleAction ? (
        <button type="button" className="btn btn-sm" onClick={handleAction}>{actionLabel}</button>
      ) : null}
    </div>
  );
}

function HealthGrid({ components = [] }) {
  return (
    <div className="operations-health">
      {components.map((component) => (
        <div className="operations-health-card" key={component.name}>
          <div className="mono operations-card-label">{component.name}</div>
          <StatusBadge kind={component.ready ? "completed" : "failed"} />
          <div className="mono operations-card-detail">{component.detail}</div>
        </div>
      ))}
    </div>
  );
}

function OperationItems({ items = [], busyKey, onOpenTarget, onResumeItem, onRetryItem }) {
  return (
    <div className="operations-list">
      {items.map((item) => (
        <div className="operations-row" key={`${item.domain}:${item.id}`}>
          <div className="operations-row-main">
            <div className="operations-row-title">{item.title}</div>
            <div className="operations-row-meta mono">
              {item.domain.replaceAll("_", " ")} · {item.id}
            </div>
          </div>
          <StatusBadge kind={item.status} />
          <div className="operations-row-actions">
            <button
              type="button"
              className="btn btn-sm"
              aria-label={`Open ${item.title}`}
              onClick={() => onOpenTarget(item.target)}
            >
              Open
            </button>
            {item.resumable ? (
              <button
                type="button"
                className="btn btn-sm"
                disabled={busyKey === `resume:${item.domain}:${item.id}`}
                aria-label={`Resume ${item.title}`}
                onClick={() => onResumeItem(item)}
              >
                Resume
              </button>
            ) : null}
            {item.retryable ? (
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={busyKey === `retry:${item.id}`}
                aria-label={`Retry ${item.title}`}
                onClick={() => onRetryItem(item)}
              >
                Retry
              </button>
            ) : null}
          </div>
        </div>
      ))}
      {!items.length ? <div className="planned">NO ACTIVE OR FAILED OPERATIONS</div> : null}
    </div>
  );
}

function Backups({ backups = [], busyKey, onCreateBackup, onVerifyBackup }) {
  return (
    <section className="operations-section">
      <div className="operations-section-head">
        <h2 className="headline operations-section-title">Backups</h2>
        <button
          type="button"
          className="btn btn-sm"
          disabled={busyKey === "backup:create"}
          onClick={onCreateBackup}
        >
          Create backup
        </button>
      </div>
      <div className="operations-list">
        {backups.map((backup) => (
          <div className="operations-row" key={backup.id}>
            <div className="operations-row-main">
              <div className="operations-row-title mono">{backup.id}</div>
              <div className="operations-row-meta mono">
                {fmtDateTime(backup.created_at)} · schema {backup.schema_version}
                {` · ${backup.database_size_bytes} bytes`}
              </div>
            </div>
            <button
              type="button"
              className="btn btn-sm"
              disabled={busyKey === `backup:${backup.id}`}
              aria-label={`Verify ${backup.id}`}
              onClick={() => onVerifyBackup(backup.id)}
            >
              Verify
            </button>
          </div>
        ))}
        {!backups.length ? <div className="planned">NO BACKUPS</div> : null}
      </div>
    </section>
  );
}

export function OperationsView({
  data,
  loading,
  error,
  onRefresh,
  onEmergencyStop,
  onResumeIntake,
  onCreateBackup,
  onVerifyBackup,
  onOpenTarget,
  onResumeItem,
  onRetryItem,
  onRelogin
}) {
  const confirm = useConfirm();
  const [busyKey, setBusyKey] = useState("");

  async function run(key, action) {
    setBusyKey(key);
    try {
      await action();
    } finally {
      setBusyKey("");
    }
  }

  async function stop() {
    const accepted = await confirm({
      title: "EMERGENCY STOP",
      message: "Block new execution and cancel active Chat, Team Run, and Job work?",
      confirmLabel: "Stop all execution",
      danger: true
    });
    if (accepted) await run("emergency-stop", onEmergencyStop);
  }

  return (
    <section className="screen operations-view">
      <div className="operations-head">
        <div>
          <h1 className="headline">Operations</h1>
          <div className="operations-sub mono">LIVE DIAGNOSTICS · RECOVERY · BACKUP</div>
        </div>
        <div className="operations-head-actions">
          <button type="button" className="btn btn-sm" disabled={loading} onClick={onRefresh}>Refresh</button>
          {data?.intake_open === false ? (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={busyKey === "resume-intake"}
              onClick={() => run("resume-intake", onResumeIntake)}
            >
              Resume intake
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-sm btn-destructive"
              disabled={busyKey === "emergency-stop"}
              onClick={stop}
            >
              Emergency stop
            </button>
          )}
        </div>
      </div>

      <ErrorPanel error={error} onRefresh={onRefresh} onRelogin={onRelogin} />
      {loading && !data ? <div className="planned">LOADING OPERATIONS</div> : null}
      {data ? (
        <>
          <div className="operations-mode mono">
            INTAKE · {data.intake_open ? "OPEN" : "STOPPED"}
            <span>ACCESS · {String(data.access_mode || "unknown").toUpperCase()}</span>
            <span>COOKIE · {data.diagnostics?.cookie_secure ? "SECURE" : "INSECURE"}</span>
            <span>
              TUNNEL · {String(data.diagnostics?.tunnel_mode || "unknown")
                .replaceAll("_", " ").toUpperCase()}
            </span>
            <span>
              WORKSPACE WRITE · {data.diagnostics?.workspace_writable ? "AVAILABLE" : "BLOCKED"}
            </span>
          </div>
          <HealthGrid components={data.health} />
          <section className="operations-section">
            <h2 className="headline operations-section-title">Execution state</h2>
            <OperationItems
              items={data.items}
              busyKey={busyKey}
              onOpenTarget={onOpenTarget}
              onResumeItem={(item) => run(`resume:${item.domain}:${item.id}`, () => onResumeItem(item))}
              onRetryItem={(item) => run(`retry:${item.id}`, () => onRetryItem(item))}
            />
          </section>
          <Backups
            backups={data.backups}
            busyKey={busyKey}
            onCreateBackup={() => run("backup:create", onCreateBackup)}
            onVerifyBackup={(id) => run(`backup:${id}`, () => onVerifyBackup(id))}
          />
        </>
      ) : null}
    </section>
  );
}
