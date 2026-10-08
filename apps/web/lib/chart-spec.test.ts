import { describe, expect, it } from "vitest";
import { describeChart, validateChartSpec } from "@/lib/chart-spec";

const cols = ["genre", "revenue"];

describe("chart spec validation (Vega-Lite subset)", () => {
  it("accepts a simple bar chart", () => {
    const s = validateChartSpec({ mark: "bar", encoding: { y: { field: "genre", type: "nominal", sort: "-x" }, x: { field: "revenue", type: "quantitative" } } }, cols);
    expect(s?.mark).toBe("bar");
  });
  it.each([
    ["inline data", { mark: "bar", data: { url: "https://evil.example/x.json" }, encoding: { x: { field: "genre", type: "nominal" } } }],
    ["transforms", { mark: "bar", transform: [{ calculate: "alert(1)", as: "x" }], encoding: {} }],
    ["unknown mark", { mark: "image", encoding: { x: { field: "genre", type: "nominal" } } }],
    ["unknown field", { mark: "bar", encoding: { x: { field: "password", type: "nominal" } } }],
    ["expression in channel", { mark: "bar", encoding: { x: { field: "genre", type: "nominal", condition: { test: "1" } } } }],
  ])("rejects %s", (_name, spec) => {
    expect(validateChartSpec(spec, cols)).toBeNull();
  });
  it("describes a chart in text for screen readers", () => {
    const s = validateChartSpec({ mark: "bar", encoding: { y: { field: "genre", type: "nominal" }, x: { field: "revenue", type: "quantitative" } } }, cols)!;
    expect(describeChart(s, cols, [["Rock", 10], ["Jazz", 3]])).toContain("Bar chart");
  });
});
