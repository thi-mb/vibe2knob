// The small markdown subset the panel renders in answers: paragraphs, **bold**, *italic*,
// `code`, and bullet or numbered lists. A heading becomes a bold paragraph, a horizontal rule
// is dropped, and everything else (tables, links, HTML, code fences) stays plain text.
// The output is plain data that React renders as text, so no HTML from the model ever runs.

export type Inline = string | { type: "strong" | "em" | "code"; children: Inline[] };

export type Block =
  | { type: "p"; children: Inline[] }
  | { type: "ul"; items: Inline[][] }
  | { type: "ol"; start: number; items: Inline[][] };

const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*(\d{1,3})[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;

/**
 * Parses an answer into blocks. While `streaming`, an unclosed `**`, `*` or backtick in the last
 * block styles the rest of it, and a trailing marker is hidden, so half-written markdown never
 * shows raw.
 */
export function parseMarkdown(text: string, streaming = false): Block[] {
  if (streaming) text = text.replace(/[*`]+$/, "");
  // First split into blocks of raw source, then parse each block's inline markers.
  type Raw = { type: "p" | "h"; src: string[] } | { type: "ul" | "ol"; start: number; items: string[][] };
  const raw: Raw[] = [];
  let open = null as Raw | null;
  const close = () => {
    if (open) raw.push(open);
    open = null;
  };

  for (const line of text.split("\n")) {
    if (!line.trim() || RULE.test(line)) {
      close();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      close();
      raw.push({ type: "h", src: [heading[1]] });
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      const type = bullet ? "ul" : "ol";
      if (open?.type !== type) {
        close();
        open = { type, start: numbered ? Number(numbered[1]) : 1, items: [] };
      }
      if (open && "items" in open) open.items.push([bullet ? bullet[1] : numbered![2]]);
      continue;
    }
    if (!open) open = { type: "p", src: [] };
    if ("items" in open) open.items[open.items.length - 1].push(line.trim());
    else open.src.push(line);
  }
  close();

  return raw.map((b, n) => {
    const last = streaming && n === raw.length - 1;
    switch (b.type) {
      case "p":
        return { type: "p", children: parseInline(b.src.join("\n"), last) };
      case "h":
        return { type: "p", children: [{ type: "strong", children: parseInline(b.src[0], last) }] };
      default: {
        const items = b.items.map((lines, k) => parseInline(lines.join("\n"), last && k === b.items.length - 1));
        return b.type === "ul" ? { type: "ul", items } : { type: "ol", start: b.start, items };
      }
    }
  });
}

const SPACE = /\s/;

/** Parses inline markers. With `open`, an unclosed marker styles everything after it. */
export function parseInline(s: string, open = false): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const push = (node: Inline) => {
    if (text) out.push(text);
    text = "";
    out.push(node);
  };

  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (c === "\\" && i + 1 < s.length && "\\*`_".includes(s[i + 1])) {
      text += s[i + 1];
      i += 2;
      continue;
    }
    if (c === "`") {
      const end = s.indexOf("`", i + 1);
      if (end > i + 1) {
        push({ type: "code", children: [s.slice(i + 1, end)] });
        i = end + 1;
        continue;
      }
      if (end < 0 && open && i + 1 < s.length) {
        push({ type: "code", children: [s.slice(i + 1)] });
        break;
      }
    }
    const strong = s.startsWith("**", i);
    if (c === "*" && opens(s, strong ? i + 2 : i + 1)) {
      const marker = strong ? "**" : "*";
      const from = i + marker.length;
      const end = findClose(s, from, marker);
      if (end >= 0) {
        push({ type: strong ? "strong" : "em", children: parseInline(s.slice(from, end)) });
        i = end + marker.length;
        continue;
      }
      if (open) {
        push({ type: strong ? "strong" : "em", children: parseInline(s.slice(from), true) });
        break;
      }
    }
    text += c;
    i++;
  }
  if (text) out.push(text);
  return out;
}

// A marker opens only when the next character is visible text, so "5 * 3" stays plain.
function opens(s: string, at: number): boolean {
  return at < s.length && !SPACE.test(s[at]) && s[at] !== "*";
}

function findClose(s: string, from: number, marker: "*" | "**"): number {
  for (let j = from + 1; j < s.length; j++) {
    if (s[j] === "`") {
      const end = s.indexOf("`", j + 1);
      if (end > 0) j = end;
      continue;
    }
    if (s[j] === "\\") {
      j++;
      continue;
    }
    if (!s.startsWith(marker, j)) continue;
    // Inside italic, a ** opens bold, so skip to its close.
    if (marker === "*" && s[j + 1] === "*" && opens(s, j + 2)) {
      const inner = findClose(s, j + 2, "**");
      if (inner >= 0) {
        j = inner + 1;
        continue;
      }
    }
    if (!SPACE.test(s[j - 1])) return j;
  }
  return -1;
}
