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
    const spy = vi.spyOn(api, "registerArtifact").mockResolvedValue({ artifact: { id: "a1" } });
    render(<MarkdownContent source={"저장했습니다: `out/cat.png`"} sessionId="sess-9" />);

    const button = screen.getByRole("button", { name: "+등록" });
    expect(button).toBeInTheDocument();

    fireEvent.click(button);
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ path: "out/cat.png", session_id: "sess-9" }));
    spy.mockRestore();
  });

  it("does not render a +등록 button for a non-registrable path", () => {
    render(<MarkdownContent source={"실행했습니다: `scripts/run.py`"} />);
    expect(screen.queryByRole("button", { name: "+등록" })).not.toBeInTheDocument();
  });
});
