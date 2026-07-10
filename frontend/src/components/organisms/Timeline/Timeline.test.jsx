import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Timeline } from "./index.jsx";

describe("Timeline ordering", () => {
  it("renders entries by creation order instead of array position", () => {
    render(
      <Timeline
        busy={false}
        entries={[
          { type: "agent", text: "answer", time: "12:01", order: 2 },
          { type: "user", text: "question", time: "12:00", order: 1 },
          {
            type: "event_row",
            label: "runtime.completed",
            detail: "session finished",
            dotColor: "#008000",
            time: "12:01:03",
            order: 3
          }
        ]}
      />
    );

    const user = screen.getByText("question");
    const finalAnswer = screen.getByText("FINAL ANSWER");
    const completion = screen.getByText("runtime.completed");

    expect(user.compareDocumentPosition(finalAnswer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(finalAnswer.compareDocumentPosition(completion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders live entries in canonical createdAtMs order regardless of array order", () => {
    const entries = [
      { type: "agent", text: "answer", createdAtMs: 300, serverOrder: 3, order: 0 },
      { type: "user", text: "question", createdAtMs: 100, serverOrder: 1, order: 1 }
    ];
    render(<Timeline entries={entries} busy={false} />);
    const blocks = document.querySelectorAll(".msg-user, .msg-agent");
    expect(blocks[0].className).toContain("msg-user");
    expect(blocks[1].className).toContain("msg-agent");
  });

  it("groups consecutive reasoning entries into one collapsed block that expands on click", () => {
    const entries = [
      { type: "reasoning", text: "step one", createdAtMs: 100, serverOrder: 1 },
      { type: "reasoning", text: "step two", createdAtMs: 101, serverOrder: 2 },
      { type: "agent", text: "final", createdAtMs: 200, serverOrder: 3 }
    ];
    render(<Timeline entries={entries} busy={false} />);
    const head = document.querySelector(".reasoning-head");
    expect(head).toBeTruthy();
    expect(head.textContent).toContain("2 steps");
    expect(document.querySelector(".reasoning-body")).toBeNull(); // 기본 접힘
    fireEvent.click(head);
    expect(document.querySelector(".reasoning-body").textContent).toContain("step one");
    expect(document.querySelector(".reasoning-body").textContent).toContain("step two");
  });
});
