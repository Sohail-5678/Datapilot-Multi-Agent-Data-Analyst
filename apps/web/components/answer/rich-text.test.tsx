import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RichText } from "@/components/answer/rich-text";

describe("RichText", () => {
  it("renders bold, bullets and never raw HTML", () => {
    const { container } = render(<RichText text={"**Rock** leads.\n\n- one\n- <img src=x onerror=alert(1)>"} />);
    expect(screen.getByText("Rock").tagName).toBe("STRONG");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("img")).toBeNull();
  });
  it("renders numbered lists", () => {
    const { container } = render(<RichText text={"Top players:\n1. Buffon (93)\n2. Rooney (93)\n3. Coupet (92)"} />);
    expect(container.querySelectorAll("ol li")).toHaveLength(3);
  });
});
