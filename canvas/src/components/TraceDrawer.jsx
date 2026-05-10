import React, { useEffect, useRef, useState } from "react";

const PHASE_TONE = {
  BOOT: "bg-slate-700/40 text-slate-200",
  DB_STATE_BEFORE: "bg-sky-700/30 text-sky-200",
  DB_STATE_AFTER: "bg-sky-700/40 text-sky-100",
  USER_ACTION: "bg-fuchsia-700/40 text-fuchsia-100",
  USER_COMMAND: "bg-fuchsia-700/60 text-fuchsia-50",
  MODEL_INPUT: "bg-indigo-700/40 text-indigo-100",
  MODEL_OUTPUT: "bg-amber-700/40 text-amber-100",
  MODEL_OUTPUT_FINAL: "bg-amber-700/60 text-amber-50",
  SQL_INTERCEPT: "bg-rose-700/50 text-rose-50",
  SQL_RESULT: "bg-emerald-700/40 text-emerald-100",
  MANIFEST_SEND: "bg-teal-700/40 text-teal-100",
  PATCH_SEND: "bg-lime-700/50 text-lime-50",
  PATCH_FAILED: "bg-rose-800/60 text-rose-50",
};

function formatData(data) {
  try {
    return JSON.stringify(data, null, 2);
  } catch (_) {
    return String(data);
  }
}

function TraceRow({ frame, expanded, onToggle }) {
  const tone = PHASE_TONE[frame.phase] ?? "bg-slate-700/40 text-slate-100";
  const summary = (() => {
    const d = frame.data;
    if (frame.phase === "SQL_INTERCEPT") return d.sql;
    if (frame.phase === "SQL_RESULT")
      return d.ok ? `ok rows=${d.rowcount}` : `error: ${d.error}`;
    if (frame.phase === "USER_ACTION")
      return `${d.user_action}${d.alert_id != null ? ` id=${d.alert_id}` : ""}`;
    if (frame.phase === "DB_STATE_BEFORE" || frame.phase === "DB_STATE_AFTER")
      return Array.isArray(d)
        ? d.map((r) => `${r.id}:${r.status}`).join("  ")
        : "";
    if (frame.phase === "MODEL_OUTPUT" || frame.phase === "MODEL_OUTPUT_FINAL") {
      const p = d.payload;
      if (p?.kind === "sql") return `kind=sql  ${p.sql}`;
      if (p?.kind === "ui")
        return `kind=ui  components=${p.components?.length ?? 0}`;
      if (d.kind === "ui") return `kind=ui  components=${d.components?.length ?? 0}`;
    }
    if (frame.phase === "MODEL_INPUT") return d.schema;
    if (frame.phase === "MANIFEST_SEND")
      return `components=${d.components?.length ?? 0}`;
    if (frame.phase === "PATCH_SEND")
      return d.ops?.map((o) => `${o.op} ${o.path}=${JSON.stringify(o.value)}`).join("  ") ?? "";
    if (frame.phase === "PATCH_FAILED") return d.error ?? "";
    return "";
  })();

  return (
    <div
      className="border-b border-slate-800/80 px-3 py-1.5 text-xs font-mono cursor-pointer hover:bg-slate-800/40"
      onClick={onToggle}
    >
      <div className="flex gap-2 items-center">
        <span className="text-slate-500 w-10 text-right">#{frame.turn}</span>
        <span className={`px-1.5 py-0.5 rounded ${tone} w-44 text-center`}>
          {frame.phase}
        </span>
        <span className="text-slate-300 truncate">{summary}</span>
      </div>
      {expanded ? (
        <pre className="mt-2 ml-12 p-2 bg-slate-950 text-slate-300 rounded overflow-x-auto whitespace-pre-wrap break-all">
{formatData(frame.data)}
        </pre>
      ) : null}
    </div>
  );
}

export default function TraceDrawer({ frames }) {
  const [open, setOpen] = useState(true);
  const [expanded, setExpanded] = useState(() => new Set());
  const endRef = useRef(null);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [frames, open]);

  function toggle(idx) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  return (
    <div className="fixed bottom-[52px] inset-x-0 z-30 border-t border-slate-800 bg-slate-950/95 backdrop-blur">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full px-4 py-2 text-left text-xs uppercase tracking-wider text-slate-400 hover:bg-slate-900"
      >
        SSM → JSON → UI trace ({frames.length} frames) {open ? "▾" : "▸"}
      </button>
      {open ? (
        <div className="max-h-72 overflow-y-auto">
          {frames.length === 0 ? (
            <div className="px-4 py-3 text-xs text-slate-500">
              No frames yet — interact with the canvas to populate.
            </div>
          ) : (
            frames.map((f, i) => (
              <TraceRow
                key={i}
                frame={f}
                expanded={expanded.has(i)}
                onToggle={() => toggle(i)}
              />
            ))
          )}
          <div ref={endRef} />
        </div>
      ) : null}
    </div>
  );
}
