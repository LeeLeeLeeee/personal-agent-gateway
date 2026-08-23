import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArtifactModal } from "./index.jsx";
import { api } from "../../../api/client.js";

const artifact = {
  id: "a1",
  type: "document",
  title: "doc.zip",
  relative_path: "files/x/doc.zip",
  mime_type: "application/zip",
  size_bytes: 1024,
  source_session_id: "s1"
};

const markdownArtifact = {
  ...artifact,
  id: "m1",
  type: "document",
  title: "review.md",
  mime_type: "text/markdown"
};

describe("ArtifactModal delete flow", () => {
  it("confirms, deletes, notifies onDeleted, then closes", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const delSpy = vi.spyOn(api, "deleteArtifact").mockResolvedValue(true);
    const onDeleted = vi.fn();
    const onClose = vi.fn();

    render(<ArtifactModal artifact={artifact} onClose={onClose} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(delSpy).toHaveBeenCalledWith("a1"));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("a1"));
    expect(onClose).toHaveBeenCalled();

    confirmSpy.mockRestore();
    delSpy.mockRestore();
  });

  it("does not delete when the confirm is cancelled", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const delSpy = vi.spyOn(api, "deleteArtifact").mockResolvedValue(true);
    const onDeleted = vi.fn();

    render(<ArtifactModal artifact={artifact} onClose={() => {}} onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    expect(delSpy).not.toHaveBeenCalled();
    expect(onDeleted).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
    delSpy.mockRestore();
  });

  it("renders markdown content and keeps technical metadata collapsed", async () => {
    vi.spyOn(api, "artifactText").mockResolvedValue("# Review\n\n- ready");
    render(<ArtifactModal artifact={markdownArtifact} onClose={() => {}} />);

    expect(await screen.findByRole("heading", { name: "Review" })).toBeInTheDocument();
    expect(screen.getByText("기술 정보")).toBeInTheDocument();
    expect(screen.getByText("files/x/doc.zip")).not.toBeVisible();
  });
});

describe("ArtifactModal provenance", () => {
  it("keeps the preview open and marks an unavailable source", async () => {
    const onClose = vi.fn();
    const onOpenSource = vi.fn().mockResolvedValue(false);
    render(
      <ArtifactModal
        artifact={artifact}
        sourceTarget={{ screen: "chat", session_id: "missing", label: "Chat 열기" }}
        onOpenSource={onOpenSource}
        onClose={onClose}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "Chat 열기" }));

    expect(await screen.findByRole("status")).toHaveTextContent("원본을 사용할 수 없음");
    expect(screen.getByRole("dialog", { name: artifact.title })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
