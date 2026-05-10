import React from "react";

const priorityClass = {
  high: "bg-red-500/20 text-red-300 ring-red-500/40",
  medium: "bg-amber-500/20 text-amber-300 ring-amber-500/40",
  low: "bg-sky-500/20 text-sky-300 ring-sky-500/40",
};

const statusClass = {
  open: "bg-rose-500/20 text-rose-300",
  acknowledged: "bg-indigo-500/20 text-indigo-300",
  resolved: "bg-emerald-500/20 text-emerald-300",
};

function derivedButtons(row) {
  if (row.status === "resolved") return [];
  const out = [
    { label: "Resolve", payload: { user_action: "resolve_alert", alert_id: row.id } },
  ];
  if (row.status === "open") {
    out.unshift({
      label: "Acknowledge",
      payload: { user_action: "acknowledge_alert", alert_id: row.id },
    });
  }
  return out;
}

export default function AlertTable({ title, rows = [], onSignal }) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 shadow-lg">
      <div className="px-5 py-3 border-b border-slate-800">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
          {title}
        </h2>
      </div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-5 py-2 w-12">#</th>
            <th className="px-5 py-2">Message</th>
            <th className="px-5 py-2 w-28">Priority</th>
            <th className="px-5 py-2 w-32">Status</th>
            <th className="px-5 py-2 w-64">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-slate-800">
              <td className="px-5 py-3 text-slate-500">{r.id}</td>
              <td className="px-5 py-3">{r.message}</td>
              <td className="px-5 py-3">
                <span
                  className={`px-2 py-0.5 rounded-md text-xs ring-1 ${
                    priorityClass[r.priority] ?? ""
                  }`}
                >
                  {r.priority}
                </span>
              </td>
              <td className="px-5 py-3">
                <span
                  className={`px-2 py-0.5 rounded-md text-xs ${
                    statusClass[r.status] ?? ""
                  }`}
                >
                  {r.status}
                </span>
              </td>
              <td className="px-5 py-3 space-x-2">
                {derivedButtons(r).map((b, i) => (
                  <button
                    key={i}
                    onClick={() => onSignal(b.payload)}
                    className="px-3 py-1 rounded-md text-xs bg-slate-800 hover:bg-slate-700 text-slate-100 ring-1 ring-slate-700"
                  >
                    {b.label}
                  </button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
