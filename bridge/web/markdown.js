(function attachMarkdownRenderer(global) {
  "use strict";

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function renderInline(value) {
    const tokens = [];
    const hold = (html) => {
      const token = `\uE000${tokens.length}\uE001`;
      tokens.push(html);
      return token;
    };
    let output = escapeHtml(value);

    output = output.replace(/`([^`\n]+)`/g, (_, code) => hold(`<code>${code}</code>`));
    output = output.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
      const safeLabel = label;
      if (/^https?:\/\//i.test(url)) {
        return hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${safeLabel}</a>`);
      }
      if (/^#[A-Za-z0-9_-]+$/.test(url)) return hold(`<a href="${url}">${safeLabel}</a>`);
      return hold(`<span class="markdown-local-link">${safeLabel}</span>`);
    });
    output = output
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
      .replace(/~~([^~\n]+)~~/g, "<del>$1</del>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s).,!?:;])/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,!?:;])/g, "$1<em>$2</em>");

    return output.replace(/\uE000(\d+)\uE001/g, (_, index) => tokens[Number(index)] || "");
  }

  function splitTableRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  }

  function isTableDivider(line) {
    const cells = splitTableRow(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function renderMarkdown(source) {
    const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
    const html = [];
    let paragraph = [];
    let listType = "";

    const flushParagraph = () => {
      if (!paragraph.length) return;
      html.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
      paragraph = [];
    };
    const closeList = () => {
      if (!listType) return;
      html.push(`</${listType}>`);
      listType = "";
    };
    const flushBlocks = () => {
      flushParagraph();
      closeList();
    };

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      const fence = line.match(/^```([A-Za-z0-9_+-]*)\s*$/);
      if (fence) {
        flushBlocks();
        const code = [];
        index += 1;
        while (index < lines.length && !/^```\s*$/.test(lines[index])) {
          code.push(lines[index]);
          index += 1;
        }
        const language = fence[1] ? ` class="language-${fence[1]}"` : "";
        html.push(`<pre><code${language}>${escapeHtml(code.join("\n"))}</code></pre>`);
        continue;
      }
      if (!line.trim()) {
        flushBlocks();
        continue;
      }
      if (index + 1 < lines.length && line.includes("|") && isTableDivider(lines[index + 1])) {
        flushBlocks();
        const headers = splitTableRow(line);
        const rows = [];
        index += 2;
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        index -= 1;
        html.push(`<div class="markdown-table-wrap"><table><thead><tr>${headers.map((cell) => `<th>${renderInline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${headers.map((_, cellIndex) => `<td>${renderInline(row[cellIndex] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushBlocks();
        const level = heading[1].length;
        html.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        continue;
      }
      if (/^>\s?/.test(line)) {
        flushBlocks();
        const quote = [];
        while (index < lines.length && /^>\s?/.test(lines[index])) {
          quote.push(lines[index].replace(/^>\s?/, ""));
          index += 1;
        }
        index -= 1;
        html.push(`<blockquote>${quote.map(renderInline).join("<br>")}</blockquote>`);
        continue;
      }
      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        flushParagraph();
        const nextType = ordered ? "ol" : "ul";
        if (listType && listType !== nextType) closeList();
        if (!listType) {
          listType = nextType;
          html.push(`<${listType}>`);
        }
        html.push(`<li>${renderInline((ordered || unordered)[1])}</li>`);
        continue;
      }
      if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(line)) {
        flushBlocks();
        html.push("<hr>");
        continue;
      }
      closeList();
      paragraph.push(line.trim());
    }
    flushBlocks();
    return html.join("") || "<p>暂无内容</p>";
  }

  const api = { renderMarkdown };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.StateDisplayMarkdown = api;
}(typeof window !== "undefined" ? window : globalThis));
