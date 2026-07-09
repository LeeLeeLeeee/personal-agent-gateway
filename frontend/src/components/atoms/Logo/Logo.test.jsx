import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Logo } from "./index.jsx";

describe("Logo", () => {
  it("renders a decorative brand mark with the green accent", () => {
    const { container } = render(<Logo />);
    const svg = container.querySelector("svg.logo");
    expect(svg).not.toBeNull();
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector('[fill="#22C55E"]')).not.toBeNull();
    expect(container.querySelector('[fill="#0A0A0A"]')).not.toBeNull();
  });

  it("uses white ink when reversed (keeps the green accent)", () => {
    const { container } = render(<Logo reversed />);
    expect(container.querySelector('[fill="#ffffff"]')).not.toBeNull();
    expect(container.querySelector('[fill="#0A0A0A"]')).toBeNull();
    expect(container.querySelector('[fill="#22C55E"]')).not.toBeNull();
  });
});
