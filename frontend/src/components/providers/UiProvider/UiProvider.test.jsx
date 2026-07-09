import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { UiProvider, useConfirm, useToast } from "./index.jsx";

function Harness() {
  const confirm = useConfirm();
  const toast = useToast();
  return (
    <div>
      <button
        type="button"
        onClick={async () => {
          const ok = await confirm({ title: "DELETE", message: "Remove it?", confirmLabel: "Delete", danger: true });
          toast(ok ? "Removed" : "Kept", ok ? "success" : "info");
        }}
      >
        act
      </button>
    </div>
  );
}

describe("UiProvider", () => {
  it("resolves the confirm modal and shows a toast on confirm", async () => {
    render(<UiProvider><Harness /></UiProvider>);

    await userEvent.click(screen.getByRole("button", { name: "act" }));
    expect(screen.getByRole("dialog", { name: "DELETE" })).toBeInTheDocument();
    expect(screen.getByText("Remove it?")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("Removed")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("resolves false on cancel and shows an info toast", async () => {
    render(<UiProvider><Harness /></UiProvider>);

    await userEvent.click(screen.getByRole("button", { name: "act" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(await screen.findByText("Kept")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
