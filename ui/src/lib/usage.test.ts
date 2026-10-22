import { describe, expect, it } from "vitest";
import type { Usage } from "../protocol/types";
import { contextFill, fmtCost, fmtTokens, fmtUsage, modelName, partRows, unsplitTokens } from "./usage";

const u = (over: Partial<Usage> = {}): Usage => ({
  input_tokens: 2000, output_tokens: 600, cache_read_tokens: 8000, cache_write_tokens: 0, thinking_tokens: 200,
  parts: { instructions: 7400, tools: 600, live: 1900, message: 100, thinking: 200, answer: 400 },
  model: "claude-haiku-5-5", cost_usd: 0.0425, cost_complete: true, ...over,
});

describe("usage formatting", () => {
  it("shortens token counts", () => {
    expect([950, 1000, 18_240, 999_000, 1_250_000, 120_000].map(fmtTokens)).toEqual(["950", "1k", "18.2k", "999k", "1.3M", "120k"]);
  });

  it("marks cost as an estimate, and as a floor when some prices were unknown", () => {
    expect(fmtCost(u())).toBe("~$0.04");
    expect(fmtCost(u({ cost_usd: 0.004 }))).toBe("<$0.01");
    expect(fmtCost(u({ cost_complete: false }))).toBe("~$0.04+");
    expect(fmtCost(u({ cost_usd: null }))).toBeNull();
    expect(fmtUsage(u())).toBe("10.6k tokens · ~$0.04");
    expect(fmtUsage(u({ cost_usd: null }))).toBe("10.6k tokens");
  });

  it("lists parts in order and counts what older answers didn't split", () => {
    expect(partRows(u()).map((r) => r.key)).toEqual(["instructions", "tools", "live", "message", "thinking", "answer"]);
    expect(unsplitTokens(u())).toBe(0);
    expect(unsplitTokens(u({ parts: {} }))).toBe(10_600);
  });

  it("calls a chat long past 150k tokens or 70% of the window", () => {
    expect(contextFill(null, 1_000_000)).toBeNull();
    expect(contextFill(40_000, 1_000_000)).toMatchObject({ share: 0.04, long: false });
    expect(contextFill(160_000, 1_000_000)?.long).toBe(true);
    expect(contextFill(150_000, 200_000)?.long).toBe(true);
    expect(contextFill(40_000, null)).toMatchObject({ share: null, long: false });
  });

  it("names models", () => {
    expect(modelName("claude-haiku-5-5")).toBe("Haiku 5.5");
    expect(modelName("claude-opus-5")).toBe("Opus 5");
    expect(modelName("claude-haiku-4-5-20251001")).toBe("Haiku 4.5");
    expect(modelName("llama3.1")).toBe("llama3.1");
  });
});
