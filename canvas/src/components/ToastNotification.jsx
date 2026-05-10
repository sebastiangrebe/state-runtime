import React from "react";

const toneClass = {
  info: "bg-sky-500/15 text-sky-200 ring-sky-500/40",
  success: "bg-emerald-500/15 text-emerald-200 ring-emerald-500/40",
  warning: "bg-amber-500/15 text-amber-200 ring-amber-500/40",
  error: "bg-rose-500/15 text-rose-200 ring-rose-500/40",
};

export default function ToastNotification({ message, tone = "info" }) {
  return (
    <div
      className={`rounded-xl ring-1 px-4 py-3 text-sm ${
        toneClass[tone] ?? toneClass.info
      }`}
    >
      {message}
    </div>
  );
}
