import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Statusbar } from "./index.jsx";
import { UiProvider } from "../../providers/UiProvider/index.jsx";

const LONG = "C:/Users/Administrator/AppData/Local/Temp/claude/session-abc/scratchpad/pag-ws";

function renderBar() {
  return render(
    <UiProvider>
      <Statusbar status={{ workspace_root: LONG }} entries={[]} busy={false} sseState="idle" />
    </UiProvider>
  );
}

describe("Statusbar workspace copy", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows only the distinguishable tail of the workspace path", () => {
    renderBar();
    expect(screen.getByText("…/scratchpad/pag-ws")).toBeInTheDocument();
    expect(screen.queryByText(LONG)).not.toBeInTheDocument();
  });

  it("copies the full path and shows a confirmation toast", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    renderBar();
    await userEvent.click(screen.getByRole("button", { name: /Copy workspace path/i }));

    expect(writeText).toHaveBeenCalledWith(LONG);
    expect(await screen.findByText("복사되었습니다")).toBeInTheDocument();
  });
});
