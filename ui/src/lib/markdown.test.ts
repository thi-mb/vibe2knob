import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "./markdown";

describe("parseInline", () => {
  it("reads bold, italic and code", () => {
    expect(parseInline("**Frequency.** The *chop* sits on `EQ Eight`.")).toEqual([
      { type: "strong", children: ["Frequency."] },
      " The ",
      { type: "em", children: ["chop"] },
      " sits on ",
      { type: "code", children: ["EQ Eight"] },
      ".",
    ]);
  });

  it("nests italic in bold and bold in italic", () => {
    expect(parseInline("**a *b* c**")).toEqual([{ type: "strong", children: ["a ", { type: "em", children: ["b"] }, " c"] }]);
    expect(parseInline("*a **b** c*")).toEqual([{ type: "em", children: ["a ", { type: "strong", children: ["b"] }, " c"] }]);
  });

  it("leaves stray and spaced markers alone", () => {
    expect(parseInline("5 * 3 and 2 * 4")).toEqual(["5 * 3 and 2 * 4"]);
    expect(parseInline("a **dangling marker")).toEqual(["a **dangling marker"]);
    expect(parseInline("one ` tick")).toEqual(["one ` tick"]);
    expect(parseInline("\\*not italic\\*")).toEqual(["*not italic*"]);
  });

  it("doesn't read markers inside code", () => {
    expect(parseInline("`**x**` and *y*")).toEqual([{ type: "code", children: ["**x**"] }, " and ", { type: "em", children: ["y"] }]);
  });

  it("styles the rest after an unclosed marker when open", () => {
    expect(parseInline("say **Move", true)).toEqual(["say ", { type: "strong", children: ["Move"] }]);
    expect(parseInline("on `Phaser-Fl", true)).toEqual(["on ", { type: "code", children: ["Phaser-Fl"] }]);
  });
});

describe("parseMarkdown", () => {
  it("splits paragraphs and keeps single line breaks", () => {
    expect(parseMarkdown("one\ntwo\n\nthree")).toEqual([
      { type: "p", children: ["one\ntwo"] },
      { type: "p", children: ["three"] },
    ]);
  });

  it("reads bullet and numbered lists, with continuation lines", () => {
    expect(parseMarkdown("Try:\n- **Sync** the phaser\n  to 2 bars\n- narrow one\n\n3. third\n4. fourth")).toEqual([
      { type: "p", children: ["Try:"] },
      { type: "ul", items: [[{ type: "strong", children: ["Sync"] }, " the phaser\nto 2 bars"], ["narrow one"]] },
      { type: "ol", start: 3, items: [["third"], ["fourth"]] },
    ]);
  });

  it("turns headings into bold paragraphs and drops rules", () => {
    expect(parseMarkdown("## Movement\n---\ntext")).toEqual([
      { type: "p", children: [{ type: "strong", children: ["Movement"] }] },
      { type: "p", children: ["text"] },
    ]);
  });

  it("keeps tables, links and HTML as plain text", () => {
    const text = "| a | b |\n[link](http://x) <b>hi</b>";
    expect(parseMarkdown(text)).toEqual([{ type: "p", children: [text] }]);
  });

  it("never shows half-written markers while streaming", () => {
    expect(parseMarkdown("**Freq", true)).toEqual([{ type: "p", children: [{ type: "strong", children: ["Freq"] }] }]);
    expect(parseMarkdown("Short answer. *", true)).toEqual([{ type: "p", children: ["Short answer. "] }]);
    expect(parseMarkdown("ok **", true)).toEqual([{ type: "p", children: ["ok "] }]);
    expect(parseMarkdown("- one\n- **tw", true)).toEqual([
      { type: "ul", items: [["one"], [{ type: "strong", children: ["tw"] }]] },
    ]);
  });

  it("only opens markers in the last block while streaming", () => {
    expect(parseMarkdown("a **b\n\nc", true)).toEqual([
      { type: "p", children: ["a **b"] },
      { type: "p", children: ["c"] },
    ]);
  });
});
