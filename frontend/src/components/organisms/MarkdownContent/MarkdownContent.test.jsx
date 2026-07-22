import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MarkdownContent } from "./index.jsx";
import { api } from "../../../api/client.js";

describe("MarkdownContent", () => {
  it("renders the structured markdown blocks used by agent messages", () => {
    render(<MarkdownContent source={[
      "## Result",
      "",
      "- one",
      "- two",
      "",
      "| Name | Value |",
      "| --- | --- |",
      "| status | ok |",
      "",
      "```js",
      "console.log('ok')",
      "```",
      "",
      "[docs](https://example.com)"
    ].join("\n")} />);

    expect(screen.getByRole("heading", { name: "Result" })).toBeInTheDocument();
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("status")).toBeInTheDocument();
    expect(screen.getByText("console.log('ok')")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "docs" })).toHaveAttribute("href", "https://example.com");
  });
});

describe("MarkdownContent path registration", () => {
  it("renders a +등록 button for a registrable path and registers on click", async () => {
    const spy = vi.spyOn(api, "registerArtifact").mockResolvedValue({
      status: 200,
      ok: true,
      data: { artifact: { id: "a1", type: "image", title: "cat.png", relative_path: "files/x/cat.png", mime_type: "image/png", size_bytes: 10, created_at: "2026-07-10T00:00:00Z" } },
    });
    render(<MarkdownContent source={"저장했습니다: `out/cat.png`"} sessionId="sess-9" />);

    const button = screen.getByRole("button", { name: "+등록" });
    expect(button).toBeInTheDocument();

    fireEvent.click(button);
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ path: "out/cat.png", session_id: "sess-9" }));
    await screen.findByRole("button", { name: "보기" });
    spy.mockRestore();
  });

  it("flips to 보기 on a 409 duplicate response", async () => {
    const spy = vi.spyOn(api, "registerArtifact").mockResolvedValue({
      status: 409,
      ok: false,
      data: { detail: { artifact: { id: "dup1", type: "image", title: "cat.png", relative_path: "files/y/cat.png", mime_type: "image/png", size_bytes: 10, created_at: "2026-07-10T00:00:00Z" } } },
    });
    render(<MarkdownContent source={"저장했습니다: `out/cat.png`"} sessionId="sess-9" />);

    const button = screen.getByRole("button", { name: "+등록" });
    fireEvent.click(button);
    await screen.findByRole("button", { name: "보기" });
    spy.mockRestore();
  });

  it("does not render a +등록 button for a non-registrable path", () => {
    render(<MarkdownContent source={"실행했습니다: `scripts/run.py`"} />);
    expect(screen.queryByRole("button", { name: "+등록" })).not.toBeInTheDocument();
  });

  it("renders paths without registration actions in read-only content", () => {
    render(<MarkdownContent source={"결과: `artifacts/report.md`"} pathRegistration={false} />);
    expect(screen.getByText("artifacts/report.md")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+등록" })).not.toBeInTheDocument();
  });

  it("does not render a +등록 button for a bare URL in plain text", () => {
    render(<MarkdownContent source={"참고: https://example.com/report.pdf 확인"} />);
    expect(screen.queryByRole("button", { name: "+등록" })).not.toBeInTheDocument();
  });

  it("does not render a +등록 button for a URL inside a code span", () => {
    render(<MarkdownContent source={"링크: `https://example.com/report.pdf`"} />);
    expect(screen.queryByRole("button", { name: "+등록" })).not.toBeInTheDocument();
  });

  it("shows 보기 (not +등록) when the path is already registered", () => {
    const registered = new Map([["out/cat.png", { id: "a1", type: "image", title: "cat.png", relative_path: "files/x/cat.png", mime_type: "image/png", size_bytes: 10, created_at: "2026-07-10T00:00:00Z" }]]);
    render(<MarkdownContent source={"저장: `out/cat.png`"} sessionId="s1" registeredByPath={registered} />);
    expect(screen.getByRole("button", { name: "보기" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+등록" })).not.toBeInTheDocument();
  });
});
