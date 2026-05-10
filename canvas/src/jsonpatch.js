// Tiny RFC 6902 applier — replace / add / remove on JSON Pointer paths.
// Mirror of engine/jsonpatch.py.

function decode(token) {
  return token.replace(/~1/g, "/").replace(/~0/g, "~");
}

function walk(doc, parts) {
  if (parts.length === 0) {
    throw new Error("empty path is unsupported");
  }
  let target = doc;
  for (let i = 0; i < parts.length - 1; i++) {
    const token = decode(parts[i]);
    if (Array.isArray(target)) {
      const idx = parseInt(token, 10);
      if (Number.isNaN(idx)) throw new Error(`non-int index ${token}`);
      target = target[idx];
    } else if (target && typeof target === "object") {
      if (!(token in target)) throw new Error(`missing key ${token}`);
      target = target[token];
    } else {
      throw new Error(`cannot descend into ${typeof target}`);
    }
  }
  return [target, decode(parts[parts.length - 1])];
}

export function applyPatch(doc, ops) {
  const out = structuredClone(doc);
  for (const op of ops) {
    if (!op.path || !op.path.startsWith("/")) {
      throw new Error(`path must begin with '/': ${op.path}`);
    }
    const parts = op.path.split("/").slice(1);
    const [parent, last] = walk(out, parts);

    if (op.op === "replace") {
      if (Array.isArray(parent)) parent[parseInt(last, 10)] = op.value;
      else parent[last] = op.value;
    } else if (op.op === "add") {
      if (Array.isArray(parent)) {
        if (last === "-") parent.push(op.value);
        else parent.splice(parseInt(last, 10), 0, op.value);
      } else {
        parent[last] = op.value;
      }
    } else if (op.op === "remove") {
      if (Array.isArray(parent)) parent.splice(parseInt(last, 10), 1);
      else delete parent[last];
    } else {
      throw new Error(`unsupported op ${op.op}`);
    }
  }
  return out;
}
