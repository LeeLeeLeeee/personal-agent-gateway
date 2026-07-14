import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocumentPreview } from "./index.jsx";

describe("DocumentPreview", () => {
  it("renders json pretty-printed", () => {
    render(<DocumentPreview open doc={{ path: "data.json", kind: "json", previewable: true, content: '{"a":1}' }} onClose={vi.fn()} />);
    expect(screen.getByText(/"a": 1/)).toBeInTheDocument();
  });

  it("shows a not-previewable message", () => {
    render(<DocumentPreview open doc={{ path: "img.png", kind: "binary", previewable: false, reason: "binary" }} onClose={vi.fn()} />);
    expect(screen.getByText(/미리보기 불가/i)).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    const { container } = render(<DocumentPreview open={false} doc={null} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
