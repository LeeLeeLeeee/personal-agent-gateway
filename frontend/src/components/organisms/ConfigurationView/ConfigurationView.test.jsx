import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfigurationView } from "./index.jsx";

describe("ConfigurationView", () => {
  it("groups low-frequency automation screens behind one section", async () => {
    const onSectionChange = vi.fn();
    const onAutomationSectionChange = vi.fn();
    render(
      <ConfigurationView
        section="automations"
        policySection="rules"
        automationSection="schedules"
        onSectionChange={onSectionChange}
        onPolicySectionChange={vi.fn()}
        onAutomationSectionChange={onAutomationSectionChange}
      >
        <div>Schedule content</div>
      </ConfigurationView>
    );

    expect(screen.getByRole("tab", { name: "Automations" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Schedule content")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Run history" }));
    expect(onAutomationSectionChange).toHaveBeenCalledWith("jobs");
  });
});
