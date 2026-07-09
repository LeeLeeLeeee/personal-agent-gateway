import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
});
