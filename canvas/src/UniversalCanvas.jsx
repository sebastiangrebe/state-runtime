import React, { useEffect, useRef, useState } from "react";
import AlertTable from "./components/AlertTable.jsx";
import ToastNotification from "./components/ToastNotification.jsx";
import MetricCard from "./components/MetricCard.jsx";
import LineChart from "./components/LineChart.jsx";
import SettingsForm from "./components/SettingsForm.jsx";
import TraceDrawer from "./components/TraceDrawer.jsx";
import Omnibox from "./components/Omnibox.jsx";
import { applyPatch } from "./jsonpatch.js";

const REGISTRY = {
  AlertTable,
  ToastNotification,
  MetricCard,
  LineChart,
  SettingsForm,
};

// WS URL derivation:
//   * served by the engine itself (production: same host:port as the page) → /ws
//   * Vite dev server on :5173 (canvas hot reload) → engine on :8000
//   * RunPod proxy: <id>-5173.proxy.runpod.net → swap to <id>-8000.proxy.runpod.net
function deriveWsUrl() {
  const env = import.meta.env?.VITE_WS_URL;
  if (env) return env;
  const loc = window.location;
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  let host = loc.host;
  if (host.startsWith("localhost:5173") || host.startsWith("127.0.0.1:5173")) {
    host = host.replace(":5173", ":8000");
  } else if (/-5173\./.test(host)) {
    host = host.replace(/-5173\./, "-8000.");
  }
  return `${proto}//${host}/ws`;
}

const WS_URL = deriveWsUrl();
const TRACE_LIMIT = 400;

export default function UniversalCanvas() {
  const [manifest, setManifest] = useState(null);
  const [connected, setConnected] = useState(false);
  const [trace, setTrace] = useState([]);
  const wsRef = useRef(null);

  useEffect(() => {
    let stopped = false;
    let retry = 0;

    function connect() {
      if (stopped) return;
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) {
          retry = Math.min(retry + 1, 6);
          setTimeout(connect, 500 * 2 ** retry);
        }
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (!data || typeof data !== "object") return;

          if (data.kind === "ui" && Array.isArray(data.components)) {
            console.log("[ws] UI manifest", data);
            setManifest(data);
          } else if (data.kind === "patch" && Array.isArray(data.ops)) {
            console.log("[ws] patch", data.ops);
            setManifest((prev) => {
              if (!prev) {
                console.warn("patch arrived before any manifest — dropping");
                return prev;
              }
              try {
                return applyPatch(prev, data.ops);
              } catch (e) {
                console.warn("patch apply failed", e, data.ops);
                return prev;
              }
            });
          } else if (data.kind === "trace") {
            console.log(`[trace #${data.turn}] ${data.phase}`, data.data);
            setTrace((prev) => {
              const next =
                prev.length >= TRACE_LIMIT
                  ? prev.slice(prev.length - TRACE_LIMIT + 1)
                  : prev.slice();
              next.push(data);
              return next;
            });
          }
        } catch (e) {
          console.warn("bad WS payload", e);
        }
      };
    }
    connect();
    return () => {
      stopped = true;
      wsRef.current?.close();
    };
  }, []);

  function pushClientTrace(phase, data) {
    setTrace((prev) => {
      const frame = { kind: "trace", turn: "→", phase, ts: Date.now() / 1000, data };
      const next =
        prev.length >= TRACE_LIMIT
          ? prev.slice(prev.length - TRACE_LIMIT + 1)
          : prev.slice();
      next.push(frame);
      return next;
    });
  }

  function sendSignal(payload) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    console.log("[ws] →", payload);
    pushClientTrace("CLIENT_SEND", payload);
    ws.send(JSON.stringify(payload));
  }

  function sendCommand(intent) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const payload = { event: "USER_COMMAND", intent };
    console.log("[ws] → COMMAND", payload);
    pushClientTrace("CLIENT_COMMAND", payload);
    ws.send(JSON.stringify(payload));
  }

  const components = manifest?.components ?? [];

  const grouped = (() => {
    const metrics = [];
    const rest = [];
    for (const c of components) {
      if (c.component === "MetricCard") metrics.push(c);
      else rest.push(c);
    }
    return { metrics, rest };
  })();

  function renderComponent(c, key) {
    const Comp = REGISTRY[c.component];
    if (!Comp) {
      return (
        <div
          key={key}
          className="rounded-md border border-rose-700/40 bg-rose-900/20 px-3 py-2 text-xs text-rose-200"
        >
          Unknown component "{c.component}" — registry miss.
        </div>
      );
    }
    return <Comp key={key} {...c} onSignal={sendSignal} />;
  }

  return (
    <div className="min-h-screen p-8 pb-40 max-w-6xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">State Runtime</h1>
          <p className="text-xs text-slate-500">
            UI is a function of the model's hidden state. No frontend logic.
          </p>
        </div>
        <span
          className={`text-xs px-2 py-1 rounded-md ring-1 ${
            connected
              ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40"
              : "bg-slate-800 text-slate-400 ring-slate-700"
          }`}
        >
          {connected ? "WS connected" : "connecting…"}
        </span>
      </header>

      {grouped.metrics.length ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {grouped.metrics.map((c, i) => renderComponent(c, `m-${i}`))}
        </div>
      ) : null}

      {grouped.rest.map((c, i) => renderComponent(c, `c-${i}`))}

      {!manifest && (
        <div className="text-sm text-slate-500">
          Waiting for first manifest from runtime…
        </div>
      )}

      <TraceDrawer frames={trace} />
      <Omnibox disabled={!connected} onSubmit={sendCommand} />
    </div>
  );
}
