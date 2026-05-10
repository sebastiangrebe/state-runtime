import React, { useState } from "react";

export default function Omnibox({ disabled, onSubmit, hint }) {
  const [text, setText] = useState("");

  function submit(e) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setText("");
  }

  return (
    <form
      onSubmit={submit}
      className="fixed bottom-0 inset-x-0 z-40 border-t border-slate-800 bg-slate-950/95 backdrop-blur"
    >
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-3">
        <span className="text-slate-500 select-none">›</span>
        <input
          autoFocus
          disabled={disabled}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            hint ?? "Tell the runtime what to render — try: show me database metrics"
          }
          className="flex-1 bg-transparent outline-none text-slate-100 placeholder-slate-500 text-sm"
        />
        <button
          type="submit"
          disabled={disabled || !text.trim()}
          className="px-3 py-1 rounded-md text-xs bg-slate-800 hover:bg-slate-700 disabled:opacity-40 ring-1 ring-slate-700 text-slate-100"
        >
          Send (⏎)
        </button>
      </div>
    </form>
  );
}
