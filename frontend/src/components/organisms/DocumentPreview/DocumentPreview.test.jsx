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

  it("renders raster images with the authenticated preview URL", () => {
    render(<DocumentPreview open doc={{
      path: "screen.png", kind: "image", previewable: true,
      preview_url: "/api/team-runs/r1/documents/image?path=screen.png"
    }} onClose={vi.fn()} />);

    expect(screen.getByRole("img", { name: "screen.png" })).toHaveAttribute(
      "src", "/api/team-runs/r1/documents/image?path=screen.png"
    );
  });

  it("renders HTML in a scriptless CSP sandbox instead of raw code", () => {
    render(<DocumentPreview open doc={{
      path: "report.html", kind: "html", previewable: true,
      content: "<h1>Report</h1><script>window.top.location='https://example.com'</script>"
    }} onClose={vi.fn()} />);

    const frame = screen.getByTitle("report.html preview");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame.getAttribute("srcdoc")).toContain("default-src 'none'");
    expect(frame.getAttribute("srcdoc")).toContain("<h1>Report</h1>");
    expect(screen.queryByText(/window\.top\.location/)).not.toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    const { container } = render(<DocumentPreview open={false} doc={null} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
