const ACTIVE_STATUSES = new Set([
  "planning",
  "running",
  "summarizing",
  "waiting_approval",
  "queued"
]);

const ATTENTION_STATUSES = new Set([
  "waiting_approval",
  "interrupted",
  "failed",
  "canceled"
]);

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function timestamp(value) {
  if (typeof value !== "string") return Number.NEGATIVE_INFINITY;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

function sortedRecent(items) {
  return [...items].sort((left, right) => timestamp(right.updated_at) - timestamp(left.updated_at));
}

function needsAttention(item) {
  return ATTENTION_STATUSES.has(item.status)
    || item.retryable === true
    || item.resumable === true
    || item.policy_status === "paused_failure"
    || item.policy_status === "paused_interrupted";
}

export function isOperationsPayload(value) {
  return isRecord(value)
    && typeof value.intake_open === "boolean"
    && Array.isArray(value.items)
    && Array.isArray(value.health)
    && isRecord(value.diagnostics);
}

export function operationsDashboardModel(data) {
  const items = data.items.filter(isRecord);
  const attentionItems = sortedRecent(items.filter(needsAttention));
  const activeItems = sortedRecent(
    items.filter((item) => ACTIVE_STATUSES.has(item.status) && !needsAttention(item))
  );
  const systemAttention = [];
  const health = data.health.filter(isRecord);

  if (data.intake_open === false) {
    systemAttention.push({
      id: "intake",
      title: "실행 intake가 중단되었습니다.",
      detail: "새 작업을 받지 않습니다.",
      kind: "failed"
    });
  }

  if (data.diagnostics.workspace_writable === false) {
    systemAttention.push({
      id: "workspace",
      title: "워크스페이스에 쓸 수 없습니다.",
      detail: "작업 결과를 저장하지 못할 수 있습니다.",
      kind: "failed"
    });
  }

  for (const component of health) {
    if (component.ready === false) {
      systemAttention.push({
        id: `health:${String(component.name || "unknown")}`,
        title: `${component.name || "알 수 없는 구성 요소"} 상태를 확인하세요.`,
        detail: typeof component.detail === "string" ? component.detail : "상태 정보가 없습니다.",
        kind: "failed"
      });
    }
  }

  return {
    activeItems,
    attentionItems,
    systemAttention,
    health,
    healthyCount: health.filter((component) => component.ready === true).length
  };
}
