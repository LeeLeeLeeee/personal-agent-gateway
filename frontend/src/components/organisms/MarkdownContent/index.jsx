import { createContext, useContext, useEffect, useState } from "react";
import { api } from "../../../api/client.js";
import { useToast } from "../../providers/UiProvider/index.jsx";
import { isRegistrablePath, makePathRe } from "../../../lib/artifactTypes.js";
import { ArtifactModal } from "../ArtifactModal/index.jsx";

let mermaidPromise = null;
let mermaidSeq = 0;

const SessionIdContext = createContext(null);
const RegistryContext = createContext({ registeredByPath: null, onRegistered: null, pathRegistration: true });

function PathChip({ path }) {
  const sessionId = useContext(SessionIdContext);
  const { registeredByPath, onRegistered, pathRegistration } = useContext(RegistryContext);
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [localArtifact, setLocalArtifact] = useState(null);
  const [open, setOpen] = useState(false);

  const artifact = localArtifact || registeredByPath?.get(path) || null;

  if (!pathRegistration) return <code className="md-code">{path}</code>;

  async function register() {
    if (saving) return;
    setSaving(true);
    try {
      const res = await api.registerArtifact({ path, session_id: sessionId });
      if (res.status === 200 && res.data?.artifact) {
        setLocalArtifact(res.data.artifact);
        toast("아티팩트로 등록되었습니다", "success");
        onRegistered?.();
      } else if (res.status === 409 && res.data?.detail?.artifact) {
        setLocalArtifact(res.data.detail.artifact);
        toast("이미 등록되어 있습니다", "info");
        onRegistered?.();
      } else {
        toast("등록에 실패했습니다", "error");
      }
    } catch {
      toast("등록에 실패했습니다", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <span className="path-chip">
      <code className="md-code">{path}</code>
      {artifact ? (
        <button type="button" className="path-chip-add" onClick={() => setOpen(true)}>보기</button>
      ) : (
        <button type="button" className="path-chip-add" onClick={register} disabled={saving}>
          {saving ? "등록 중…" : "+등록"}
        </button>
      )}
      {open && artifact ? <ArtifactModal artifact={artifact} onClose={() => setOpen(false)} onDeleted={() => { setLocalArtifact(null); onRegistered?.(); }} /> : null}
    </span>
  );
}

// Split a plain-text segment into strings and <PathChip> nodes for registrable paths.
function splitPaths(text, keyPrefix) {
  const re = makePathRe();
  const out = [];
  let last = 0;
  let match;
  while ((match = re.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    if (match[0].includes("://")) out.push(match[0]); // URL, not a local path
    else out.push(<PathChip key={`${keyPrefix}-path-${match.index}`} path={match[0]} />);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function ensureMermaid() {
  if (typeof window !== "undefined" && window.mermaid) return Promise.resolve(window.mermaid);
  if (mermaidPromise) return mermaidPromise;
  mermaidPromise = new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = "/static/vendor/mermaid.min.js";
    el.onload = () => {
      window.mermaid.initialize({ startOnLoad: false, theme: "neutral", securityLevel: "loose" });
      resolve(window.mermaid);
    };
    el.onerror = () => reject(new Error("mermaid load failed"));
    document.head.appendChild(el);
  });
  return mermaidPromise;
}

function highlightHtml(code, lang) {
  const hljs = typeof window !== "undefined" ? window.hljs : null;
  if (!hljs) return null;
  try {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
    return hljs.highlightAuto(code).value;
  } catch {
    return null;
  }
}

function useCopy() {
  const toast = useToast();
  return (code) => {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(code)
        .then(() => toast("복사되었습니다", "success"))
        .catch(() => toast("복사에 실패했습니다", "error"));
    } else {
      toast("복사에 실패했습니다", "error");
    }
  };
}

function CodeBody({ lang, code }) {
  const html = highlightHtml(code, lang);
  return (
    <pre className="md-pre">
      {html
        ? <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
        : <code className="hljs">{code}</code>}
    </pre>
  );
}

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
    if (match.index > last) out.push(...splitPaths(text.slice(last, match.index), `${match.index}-pre`));
    if (match[1] !== undefined) {
      out.push(
        isRegistrablePath(match[1])
          ? <PathChip key={`${match.index}-path`} path={match[1].trim()} />
          : <code className="md-code" key={`${match.index}-code`}>{match[1]}</code>
      );
    } else if (match[2] !== undefined) out.push(<strong key={`${match.index}-strong`}>{match[2]}</strong>);
    else if (match[3] !== undefined) out.push(<em key={`${match.index}-em`}>{match[3]}</em>);
    else out.push(<a key={`${match.index}-link`} href={match[5]} target="_blank" rel="noopener noreferrer">{match[4]}</a>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(...splitPaths(text.slice(last), "tail"));
  return out;
}

function splitRow(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function CodeBlock({ lang, code }) {
  const copy = useCopy();
  return (
    <div className="code-wrap">
      <div className="code-bar">
        <span className="code-lang">{lang || "text"}</span>
        <button className="code-copy" type="button" onClick={() => copy(code)}>COPY</button>
      </div>
      <CodeBody lang={lang} code={code} />
    </div>
  );
}

function MermaidBlock({ code }) {
  const copy = useCopy();
  const [shown, setShown] = useState(false);
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!shown) return undefined;
    let cancelled = false;
    setError("");
    ensureMermaid()
      .then((mermaid) => {
        mermaidSeq += 1;
        return mermaid.render(`mmd-${mermaidSeq}`, code);
      })
      .then((result) => { if (!cancelled) setSvg(result.svg); })
      .catch((err) => { if (!cancelled) setError(String(err?.message || err)); });
    return () => { cancelled = true; };
  }, [shown, code]);

  return (
    <div className="mermaid-wrap">
      <div className="code-bar">
        <span className="code-lang">mermaid</span>
        <div className="code-actions">
          <button className="code-copy" type="button" onClick={() => copy(code)}>COPY</button>
          <button className="code-copy" type="button" onClick={() => setShown((value) => !value)}>{shown ? "▾ 코드 보기" : "▸ 그래프 보기"}</button>
        </div>
      </div>
      {shown ? (
        error
          ? <pre className="md-pre mermaid-error"><code>{error}</code></pre>
          : svg
            ? <div className="mermaid-render" dangerouslySetInnerHTML={{ __html: svg }} />
            : <div className="mermaid-render mermaid-loading">그래프 렌더링 중…</div>
      ) : (
        <CodeBody lang="mermaid" code={code} />
      )}
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

export function MarkdownContent({
  source,
  sessionId = null,
  registeredByPath = null,
  onRegistered = null,
  pathRegistration = true
}) {
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

  return (
    <SessionIdContext.Provider value={sessionId}>
      <RegistryContext.Provider value={{ registeredByPath, onRegistered, pathRegistration }}>
        <div className="md">{nodes}</div>
      </RegistryContext.Provider>
    </SessionIdContext.Provider>
  );
}
