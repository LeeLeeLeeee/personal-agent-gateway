import { useState } from "react";

function hashStr(value) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) | 0;
  return String(hash);
}

function inlineNodes(text) {
  const out = [];
  const re = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*\n]+)\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let last = 0;
  let match;
  while ((match = re.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    if (match[1] !== undefined) out.push(<code className="md-code" key={`${match.index}-code`}>{match[1]}</code>);
    else if (match[2] !== undefined) out.push(<strong key={`${match.index}-strong`}>{match[2]}</strong>);
    else if (match[3] !== undefined) out.push(<em key={`${match.index}-em`}>{match[3]}</em>);
    else out.push(<a key={`${match.index}-link`} href={match[5]} target="_blank" rel="noopener noreferrer">{match[4]}</a>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function splitRow(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function CodeBlock({ lang, code }) {
  return (
    <div className="code-wrap">
      <div className="code-bar">
        <span className="code-lang">{lang || "text"}</span>
        <button className="code-copy" type="button" onClick={() => navigator.clipboard?.writeText(code)}>COPY</button>
      </div>
      <pre className="md-pre"><code className="hljs">{code}</code></pre>
    </div>
  );
}

function MermaidBlock({ code }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="mermaid-wrap">
      <div className="code-bar">
        <span className="code-lang">mermaid</span>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="code-copy" type="button" onClick={() => navigator.clipboard?.writeText(code)}>COPY</button>
          <button className="code-copy" type="button" onClick={() => setShown((value) => !value)}>{shown ? "▾ 코드 보기" : "▸ 그래프 보기"}</button>
        </div>
      </div>
      <CodeBlock lang="mermaid" code={code} />
    </div>
  );
}

function TableBlock({ rows }) {
  const head = rows[0] || [];
  const body = rows.slice(1);
  return (
    <div className="table-wrap">
      <div className="table-scroll">
        <table className="md-table">
          <thead><tr>{head.map((cell, index) => <th key={index}>{inlineNodes(cell)}</th>)}</tr></thead>
          <tbody>{body.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, index) => <td key={index}>{inlineNodes(cell)}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

function paragraphNodes(lines, key) {
  return (
    <p key={key}>
      {lines.map((line, index) => (
        <span key={index}>
          {index ? <br /> : null}
          {inlineNodes(line)}
        </span>
      ))}
    </p>
  );
}

export function MarkdownContent({ source }) {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  const nodes = [];
  const isUl = (line) => /^\s*[-*]\s+/.test(line);
  const isOl = (line) => /^\s*\d+\.\s+/.test(line);
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (/^```/.test(line)) {
      const lang = line.replace(/^```/, "").trim().split(/\s+/)[0];
      index += 1;
      const buffer = [];
      let closed = false;
      while (index < lines.length) {
        if (/^```\s*$/.test(lines[index])) {
          closed = true;
          index += 1;
          break;
        }
        buffer.push(lines[index]);
        index += 1;
      }
      const code = buffer.join("\n");
      nodes.push(lang === "mermaid" && closed ? <MermaidBlock key={nodes.length} code={code} /> : <CodeBlock key={nodes.length} lang={lang} code={code} />);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const Tag = `h${heading[1].length}`;
      nodes.push(<Tag key={nodes.length}>{inlineNodes(heading[2])}</Tag>);
      index += 1;
      continue;
    }
    if (/\|/.test(line) && index + 1 < lines.length && /-/.test(lines[index + 1]) && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1])) {
      const rows = [splitRow(line)];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim() !== "") {
        rows.push(splitRow(lines[index]));
        index += 1;
      }
      nodes.push(<TableBlock key={nodes.length} rows={rows} />);
      continue;
    }
    if (isUl(line)) {
      const items = [];
      while (index < lines.length && isUl(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      nodes.push(<ul key={nodes.length}>{items.map((item, itemIndex) => <li key={itemIndex}>{inlineNodes(item)}</li>)}</ul>);
      continue;
    }
    if (isOl(line)) {
      const items = [];
      while (index < lines.length && isOl(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+\.\s+/, ""));
        index += 1;
      }
      nodes.push(<ol key={nodes.length}>{items.map((item, itemIndex) => <li key={itemIndex}>{inlineNodes(item)}</li>)}</ol>);
      continue;
    }
    if (/^\s*$/.test(line)) {
      index += 1;
      continue;
    }
    const paragraph = [];
    while (index < lines.length && !/^\s*$/.test(lines[index]) && !/^```/.test(lines[index]) && !/^#{1,6}\s/.test(lines[index]) && !isUl(lines[index]) && !isOl(lines[index])) {
      paragraph.push(lines[index]);
      index += 1;
    }
    nodes.push(paragraphNodes(paragraph, `${nodes.length}-${hashStr(paragraph.join("\n"))}`));
  }

  return <div className="md">{nodes}</div>;
}
