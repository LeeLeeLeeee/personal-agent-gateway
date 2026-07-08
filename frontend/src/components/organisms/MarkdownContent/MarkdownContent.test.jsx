import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownContent } from "./index.jsx";

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
