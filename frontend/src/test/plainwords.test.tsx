import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PlainWords } from "@/components/PlainWords";

const wrap = (ui: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

function stub(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
    JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }))));
}

describe("PlainWords", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks for nothing until the operator clicks (an explanation can cost a model call)", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    wrap(<PlainWords kind="decisions" id={1} />);
    expect(screen.getByTestId("explain-btn")).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("marks a model-written explanation as such, with the model name", async () => {
    stub({ text: "Autopilot is topping up Mirpur with 1,130 L.", source: "gemini",
           model: "gemini-3.5-flash-lite", latency_ms: 1438, cached: false, note: "", grounded_in: {} });
    wrap(<PlainWords kind="decisions" id={1} />);
    fireEvent.click(screen.getByTestId("explain-btn"));
    await waitFor(() => expect(screen.getByTestId("explain-text")).toHaveTextContent("topping up Mirpur"));
    expect(screen.getByTestId("explain-source")).toHaveTextContent("re-worded by gemini-3.5-flash-lite");
  });

  it("says plainly when the operator is reading the platform's own summary, and why", async () => {
    stub({ text: "Autopilot has dispatched 1,130 L of DIESEL from gazipur to mirpur.", source: "template",
           model: "", latency_ms: 0, cached: false,
           note: "the assistant's wording contained a number (45) the platform did not compute", grounded_in: {} });
    wrap(<PlainWords kind="decisions" id={2} />);
    fireEvent.click(screen.getByTestId("explain-btn"));
    await waitFor(() => expect(screen.getByTestId("explain-source")).toHaveTextContent("the platform's own summary"));
    expect(screen.getByTestId("explain-note")).toHaveTextContent("did not compute");
    expect(screen.getByTestId("explain-source")).not.toHaveTextContent("re-worded");
  });

  it("keeps the operator's own data on screen when the explanation itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("nope", { status: 500 }))));
    wrap(<PlainWords kind="incidents" id={3} />);
    fireEvent.click(screen.getByTestId("explain-btn"));
    await waitFor(() => expect(screen.getByTestId("explain-box")).toHaveTextContent("details above are unchanged"));
  });
});
