export function emptyChatSessionState() {
  return {
    entries: [],
    pendingApproval: null,
    busy: false,
    turnStart: null,
    turnEnd: null,
    turnStreamed: false,
    turnHadAgent: false,
    nextLocalOrder: 0,
    lastServerEventId: null,
    lastLoadedAt: null
  };
}

export function withSessionConfigStatus(nextStatus, nextConfig) {
  if (!nextConfig) return nextStatus;
  if (nextConfig.source !== "explicit") {
    return { ...(nextStatus || {}), session_config: nextConfig };
  }
  return {
    ...(nextStatus || {}),
    provider: nextConfig.agent_id ?? nextStatus?.provider,
    model: nextConfig.model ?? nextStatus?.model,
    session_config: nextConfig
  };
}
