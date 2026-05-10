import React, { useState } from "react";

export default function SettingsForm({
  title,
  fields = [],
  submit_label = "Save",
  submit_action = "save_settings",
  onSignal,
}) {
  const [values, setValues] = useState(() =>
    Object.fromEntries(fields.map((f) => [f.name, f.value ?? ""]))
  );

  function update(name, v) {
    setValues((prev) => ({ ...prev, [name]: v }));
  }

  function submit(e) {
    e.preventDefault();
    onSignal({
      user_action: submit_action,
      note: JSON.stringify(values),
    });
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-slate-800 bg-slate-900/60 px-5 py-4 shadow-lg space-y-3"
    >
      <div className="text-xs uppercase tracking-wider text-slate-400">
        {title}
      </div>
      {fields.map((f) => (
        <label key={f.name} className="block text-sm">
          <span className="block text-xs text-slate-400 mb-1">{f.label}</span>
          {f.type === "toggle" ? (
            <input
              type="checkbox"
              checked={values[f.name] === "true"}
              onChange={(e) => update(f.name, e.target.checked ? "true" : "false")}
              className="h-4 w-4 rounded border-slate-700 bg-slate-800"
            />
          ) : (
            <input
              type={f.type === "number" ? "number" : "text"}
              value={values[f.name] ?? ""}
              onChange={(e) => update(f.name, e.target.value)}
              className="w-full rounded-md bg-slate-950/60 ring-1 ring-slate-800 px-2 py-1 text-slate-100 focus:outline-none focus:ring-sky-500"
            />
          )}
        </label>
      ))}
      <button
        type="submit"
        className="px-3 py-1.5 rounded-md text-xs bg-sky-600 hover:bg-sky-500 text-white"
      >
        {submit_label}
      </button>
    </form>
  );
}
