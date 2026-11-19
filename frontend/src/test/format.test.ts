import { liters, pct, hours, simClock } from "@/lib/format";

describe("format", () => {
  it("formats liters with separators", () => expect(liters(12345.6)).toBe("12,346 L"));
  it("formats percents", () => expect(pct(0.9533)).toBe("95.3%"));
  it("formats hours", () => {
    expect(hours(0.5)).toBe("30 min");
    expect(hours(6.2)).toBe("6.2 h");
    expect(hours(Infinity)).toBe("> horizon");
  });
  it("matches the python sim clock", () => expect(simClock(140, 15)).toBe("Day 2 11:00 · t=140"));
});
