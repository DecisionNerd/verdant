import path from "node:path";

export function dataRoot(): string {
  return process.env.DATA_DIR ?? "/data";
}

export function inputsDir(): string {
  return path.join(dataRoot(), "inputs");
}

export function outputsDir(): string {
  return path.join(dataRoot(), "outputs");
}

export function datasetsDir(): string {
  return path.join(dataRoot(), "datasets");
}

export function jobsDir(): string {
  return path.join(dataRoot(), "jobs");
}

/** Map a host-relative `data/...` or absolute path to a container path under DATA_DIR. */
export function resolveDataPath(input: string): string {
  const DATA_DIR = dataRoot();
  if (path.isAbsolute(input)) {
    if (input === DATA_DIR || input.startsWith(DATA_DIR + path.sep) || input.startsWith(DATA_DIR + "/")) {
      return input;
    }
    const normalized = input.replace(/\\/g, "/");
    const marker = "/data/";
    const idx = normalized.lastIndexOf(marker);
    if (idx !== -1) {
      return path.join(DATA_DIR, normalized.slice(idx + marker.length));
    }
    return input;
  }

  const normalized = input.replace(/^\.\//, "").replace(/\\/g, "/");
  if (normalized === "data") return DATA_DIR;
  if (normalized.startsWith("data/")) {
    return path.join(DATA_DIR, normalized.slice("data/".length));
  }
  return path.join(DATA_DIR, normalized);
}

/** Convert a container path under DATA_DIR to a host-relative `data/...` path. */
export function toHostRelative(containerPath: string): string {
  const DATA_DIR = dataRoot();
  const normalized = containerPath.replace(/\\/g, "/");
  const root = DATA_DIR.replace(/\\/g, "/").replace(/\/$/, "");
  if (normalized === root) return "data";
  if (normalized.startsWith(root + "/")) {
    return path.posix.join("data", normalized.slice(root.length + 1));
  }
  return containerPath;
}
