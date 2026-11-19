import { render, screen } from "@testing-library/react";
import { StatusPill } from "@/components/StatusPill";

it("shows text, not only colour", () => {
  render(<StatusPill status="Down" />);
  expect(screen.getByTestId("status-pill")).toHaveTextContent("Down");
});
