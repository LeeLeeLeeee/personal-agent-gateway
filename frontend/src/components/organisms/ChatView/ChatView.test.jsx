import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatView } from "./index.jsx";

const sessions = [{
  id: "session-1",
  title: "Main chat",
  status: "idle",
  message_count: 0,
  is_active: true,
  created_at: "2026-07-08T01:00:00Z"
}];

function props(entries) {
  return {
    agents: [],
    sessions,
    sessionConfig: null,
    sessionConfigError: "",
    entries,
    busy: false,
    turnStart: null,
    turnEnd: null,
    pendingApproval: null,
    turnStreamed: false,
    onSessionConfigChange: vi.fn(),
    onSessionConfigRetry: vi.fn(),
    onSend: vi.fn(),
    onSearch: vi.fn(),
    onActivate: vi.fn(),
    onReset: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    onResolveApproval: vi.fn()
  };
}

function sizeTranscript(node, scrollHeight = 500, clientHeight = 100) {
  Object.defineProperty(node, "scrollHeight", { configurable: true, value: scrollHeight });
  Object.defineProperty(node, "clientHeight", { configurable: true, value: clientHeight });
}

describe("ChatView transcript follow behavior", () => {
  it("keeps following new entries while the user is near the bottom", () => {
    const { container, rerender } = render(<ChatView {...props([{ type: "user", text: "hello", time: "12:00" }])} />);
    const transcript = container.querySelector(".transcript");
    sizeTranscript(transcript);
    transcript.scrollTop = 400;
    fireEvent.scroll(transcript);

    rerender(<ChatView {...props([
      { type: "user", text: "hello", time: "12:00" },
      { type: "agent", text: "answer", time: "12:01" }
    ])} />);

    expect(transcript.scrollTop).toBe(500);
  });

  it("does not pull the user back down while they are reading older entries", () => {
    const { container, rerender } = render(<ChatView {...props([{ type: "user", text: "hello", time: "12:00" }])} />);
    const transcript = container.querySelector(".transcript");
    sizeTranscript(transcript);
    transcript.scrollTop = 120;
    fireEvent.scroll(transcript);

    rerender(<ChatView {...props([
      { type: "user", text: "hello", time: "12:00" },
      { type: "agent", text: "answer", time: "12:01" }
    ])} />);

    expect(transcript.scrollTop).toBe(120);
  });

  it("marks completed agent output as the final answer", () => {
    render(<ChatView {...props([{ type: "agent", text: "final text", time: "12:01" }])} />);

    expect(screen.getByText("FINAL ANSWER")).toBeInTheDocument();
    expect(screen.getByText("FINAL ANSWER").closest(".msg-agent-final")).not.toBeNull();
  });
});

describe("ChatView working indicator", () => {
  it("shows a live working indicator with elapsed time while busy", () => {
    render(<ChatView {...props([])} busy turnStart={Date.now() - 5000} turnStreamed />);
    const indicator = document.querySelector(".working-indicator");
    expect(indicator).toBeTruthy();
    expect(indicator.textContent).toContain("WORKING");
    expect(indicator.textContent).toContain("esc to interrupt");
  });

  it("hides the working indicator when idle", () => {
    render(<ChatView {...props([])} busy={false} />);
    expect(document.querySelector(".working-indicator")).toBeNull();
  });
});
