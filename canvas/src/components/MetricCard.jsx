import React from "react";

export default function MetricCard({ label, value, sub }) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 px-5 py-4 shadow-lg">
      <div className="text-xs uppercase tracking-wider text-slate-400">
        {label}
      </div>
      <div className="mt-1 text-3xl font-semibold text-slate-50">{value}</div>
      {sub ? <div className="mt-1 text-xs text-slate-400">{sub}</div> : null}
    </div>
  );
}
