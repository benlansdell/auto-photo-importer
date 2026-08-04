async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").toLocaleString();
  } catch {
    return iso;
  }
}

function setState(state) {
  const dot = document.getElementById("state-dot");
  const label = document.getElementById("state-label");
  dot.className = "state-dot " + (state || "waiting");
  label.textContent = state || "waiting";
}

function renderProgress(progress) {
  const wrap = document.getElementById("progress-wrap");
  const fill = document.getElementById("progress-fill");
  const text = document.getElementById("progress-text");
  const total = progress.files_total || 0;
  const done = progress.files_imported || progress.files_downloaded || 0;
  if (!total && !progress.message) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  fill.style.width = pct + "%";
  text.textContent = [
    progress.message,
    total ? `${done}/${total}` : null,
    progress.current_file,
    progress.files_skipped ? `skipped ${progress.files_skipped}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

async function refreshStatus() {
  const data = await fetchJSON("/api/status");
  setState(data.state);
  document.getElementById("message").textContent = data.progress?.message || data.state;
  document.getElementById("camera").textContent = data.camera_name || "—";
  document.getElementById("port").textContent = data.camera_port || "—";
  document.getElementById("last-import").textContent = fmtTime(
    data.last_import_at || data.stats?.last_import_at
  );
  document.getElementById("total-imports").textContent =
    data.stats?.total_imports != null ? String(data.stats.total_imports) : "—";
  renderProgress(data.progress || {});
  const err = document.getElementById("error");
  if (data.last_error) {
    err.hidden = false;
    err.textContent = data.last_error;
  } else {
    err.hidden = true;
  }
}

async function refreshLogs() {
  const data = await fetchJSON("/api/logs?limit=200");
  const el = document.getElementById("logs");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
  el.textContent = (data.logs || []).join("\n");
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function sessionsTable(rows) {
  if (!rows?.length) return '<p class="muted">No sessions yet</p>';
  const body = rows
    .map(
      (r) => `<tr>
      <td>${fmtTime(r.started_at)}</td>
      <td>${r.camera_name || "—"}</td>
      <td>${r.files_imported ?? 0} / skip ${r.files_skipped ?? 0}</td>
      <td class="${r.status === "error" ? "err" : "ok"}">${r.status}</td>
    </tr>`
    )
    .join("");
  return `<table><thead><tr>
    <th>Started</th><th>Camera</th><th>Files</th><th>Status</th>
  </tr></thead><tbody>${body}</tbody></table>`;
}

function importsTable(rows) {
  if (!rows?.length) return '<p class="muted">No imports yet</p>';
  const body = rows
    .map(
      (r) => `<tr>
      <td>${r.filename}</td>
      <td>${r.destination || "—"}</td>
      <td>${fmtTime(r.import_time)}</td>
    </tr>`
    )
    .join("");
  return `<table><thead><tr>
    <th>File</th><th>Path</th><th>Imported</th>
  </tr></thead><tbody>${body}</tbody></table>`;
}

async function refreshHistory() {
  const data = await fetchJSON("/api/history?limit=40");
  document.getElementById("sessions").innerHTML = sessionsTable(data.sessions);
  document.getElementById("imports").innerHTML = importsTable(data.imports);
}

document.getElementById("btn-import").addEventListener("click", async () => {
  const btn = document.getElementById("btn-import");
  btn.disabled = true;
  try {
    await fetchJSON("/api/import", { method: "POST" });
  } finally {
    setTimeout(() => (btn.disabled = false), 1500);
  }
});

document.getElementById("btn-eject").addEventListener("click", async () => {
  await fetchJSON("/api/eject", { method: "POST" });
});

async function tick() {
  try {
    await Promise.all([refreshStatus(), refreshLogs()]);
  } catch (e) {
    document.getElementById("message").textContent = "Dashboard offline: " + e.message;
  }
}

tick();
refreshHistory();
setInterval(tick, 2000);
setInterval(refreshHistory, 8000);
