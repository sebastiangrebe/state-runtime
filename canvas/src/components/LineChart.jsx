import React from "react";

export default function LineChart({ title, series = [], caption }) {
  const max = Math.max(1, ...series);
  const min = Math.min(0, ...series);
  const span = max - min || 1;
  const w = 600;
  const h = 140;
  const stepX = series.length > 1 ? w / (series.length - 1) : w;
  const path = series
    .map((v, i) => {
      const x = i * stepX;
      const y = h - ((v - min) / span) * (h - 10) - 5;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 px-5 py-4 shadow-lg">
      <div className="text-xs uppercase tracking-wider text-slate-400">
        {title}
      </div>
      <div className="mt-3 rounded-md bg-slate-950/60 ring-1 ring-slate-800 p-3">
        {series.length === 0 ? (
          <div className="h-[140px] flex items-center justify-center text-xs text-slate-500">
            Chart Placeholder — no series data
          </div>
        ) : (
          <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-[140px]">
            <path d={path} fill="none" stroke="rgb(56 189 248)" strokeWidth="2" />
          </svg>
        )}
      </div>
      {caption ? (
        <div className="mt-2 text-xs text-slate-400">{caption}</div>
      ) : null}
    </div>
  );
}
