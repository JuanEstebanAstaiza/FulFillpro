const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

let me = null;

async function init() {
  me = await loadSession();
  if (!me) {
    location.href = "/ops";
    return;
  }
  if (me.role !== "admin") {
    API.clearTokens();
    location.href = "/ops";
    return;
  }
  $("#user-label").textContent = me.email;
  $("#btn-logout").addEventListener("click", async () => {
    await API.logout();
    location.href = "/ops";
  });

  $$(".tab[data-tab]").forEach((t) =>
    t.addEventListener("click", () => {
      $$(".tab[data-tab]").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      $$("[data-panel]").forEach((p) => p.classList.add("hidden"));
      const panel = $(`[data-panel="${t.dataset.tab}"]`);
      if (panel) panel.classList.remove("hidden");
    })
  );

  $("#form-license").addEventListener("submit", createLicense);
  $("#template").addEventListener("change", applyTemplateHints);
  applyTemplateHints();

  $("#btn-backup-refresh")?.addEventListener("click", () => loadBackupInfo());
  $("#btn-backup-download")?.addEventListener("click", downloadBackup);
  $("#btn-backup-inspect")?.addEventListener("click", inspectBackup);
  $("#btn-backup-restore")?.addEventListener("click", restoreBackup);
  $("#backup-include-storage")?.addEventListener("change", () => loadBackupInfo());

  // Cargar estimación al abrir pestaña backup
  $$(".tab[data-tab]").forEach((t) =>
    t.addEventListener("click", () => {
      if (t.dataset.tab === "backup") loadBackupInfo();
    })
  );

  await Promise.all([
    loadOverview(),
    loadCompanyUsage(),
    loadLicenses(),
    loadUsers(),
    loadLogs(),
    loadIncidents(),
  ]);
}

function showAdminAlert(message, type = "error") {
  const el = $("#admin-alert");
  if (!el) return;
  el.className = `alert alert-${type === "ok" ? "ok" : type === "info" ? "info" : "error"}`;
  el.textContent = message;
  el.classList.remove("hidden");
}

function fmtBytes(n) {
  const b = Number(n) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

async function loadBackupInfo() {
  const box = $("#backup-info");
  if (!box) return;
  const include = $("#backup-include-storage")?.checked !== false;
  try {
    const d = await API.request(`/api/admin/backup/info?include_storage=${include ? "true" : "false"}`);
    const counts = d.table_counts || {};
    const topTables = Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([k, v]) => `${k}: ${v}`)
      .join(" · ");
    box.innerHTML = `
      <div class="stat"><div class="label">Filas BD</div><div class="value">${d.total_rows || 0}</div></div>
      <div class="stat"><div class="label">Archivos storage</div><div class="value">${include ? d.storage_files || 0 : "—"}</div>
        <div class="hint">${include ? fmtBytes(d.storage_bytes) : "omitido"}</div></div>
      <div class="stat"><div class="label">Storage estimado</div><div class="value">${include ? (d.storage_mb || 0) + " MB" : "0"}</div></div>
      <div class="stat"><div class="label">Formato</div><div class="value">v${d.format_version || 1}</div>
        <div class="hint">${escape(topTables || "sin datos")}</div></div>
    `;
    if (d.warning) {
      showAdminAlert(d.warning, "info");
    }
  } catch (err) {
    box.innerHTML = `<p class="muted">${escape(err.message)}</p>`;
  }
}

async function downloadBackup() {
  const btn = $("#btn-backup-download");
  const status = $("#backup-download-status");
  const include = $("#backup-include-storage")?.checked !== false;
  if (btn) btn.disabled = true;
  if (status) status.textContent = "Generando backup… puede tardar si hay muchos archivos.";
  try {
    const res = await fetch(`/api/admin/backup/download?include_storage=${include ? "true" : "false"}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${API.token}` },
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        detail = j.detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail || "Error al generar backup");
    }
    const blob = await res.blob();
    const dispo = res.headers.get("Content-Disposition") || "";
    const m = /filename="?([^";]+)"?/i.exec(dispo);
    const name = m ? m[1] : `fulfillpro-backup-${Date.now()}.zip`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
    if (status) status.textContent = `Descargado: ${name} (${fmtBytes(blob.size)})`;
    showAdminAlert(`Backup descargado: ${name}`, "ok");
  } catch (err) {
    if (status) status.textContent = "";
    showAdminAlert(err.message || "Error en backup");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function inspectBackup() {
  const input = $("#restore-file");
  const out = $("#backup-restore-result");
  if (!input?.files?.length) {
    showAdminAlert("Selecciona un archivo .zip primero.");
    return;
  }
  const fd = new FormData();
  fd.append("file", input.files[0]);
  try {
    const data = await API.request("/api/admin/backup/inspect", { method: "POST", body: fd });
    if (out) out.textContent = JSON.stringify(data, null, 2);
    showAdminAlert(
      `ZIP válido · filas≈${data.total_rows ?? "?"} · storage entries=${data.storage_entries ?? 0}`,
      "ok"
    );
  } catch (err) {
    if (out) out.textContent = err.message;
    showAdminAlert(err.message);
  }
}

async function restoreBackup() {
  const input = $("#restore-file");
  const phrase = ($("#restore-phrase")?.value || "").trim();
  const out = $("#backup-restore-result");
  const btn = $("#btn-backup-restore");
  if (!input?.files?.length) {
    showAdminAlert("Selecciona un archivo .zip de backup.");
    return;
  }
  if (phrase.toUpperCase() !== "RESTAURAR") {
    showAdminAlert('Debes escribir exactamente RESTAURAR en el campo de confirmación.');
    return;
  }
  if (
    !confirm(
      "¿Seguro? Esto REEMPLAZA los datos actuales de la plataforma. No se puede deshacer fácilmente."
    )
  ) {
    return;
  }
  if (btn) btn.disabled = true;
  if (out) out.textContent = "Restaurando… no cierres esta ventana.";
  try {
    const fd = new FormData();
    fd.append("file", input.files[0]);
    fd.append("confirm_phrase", phrase);
    fd.append(
      "include_storage",
      $("#restore-include-storage")?.checked !== false ? "true" : "false"
    );
    const data = await API.request("/api/admin/backup/restore", { method: "POST", body: fd });
    if (out) out.textContent = JSON.stringify(data, null, 2);
    showAdminAlert(data.message || "Restauración completada. Cierra sesión y vuelve a entrar.", "ok");
  } catch (err) {
    if (out) out.textContent = err.message;
    showAdminAlert(err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

async function loadOverview() {
  const d = await API.request("/api/admin/monitoring/overview");
  $("#overview").innerHTML = `
    <div class="stats">
      <div class="stat"><div class="label">Usuarios</div><div class="value">${d.total_users}</div></div>
      <div class="stat"><div class="label">Licencias activas</div><div class="value">${d.active_licenses}</div></div>
      <div class="stat">
        <div class="label">Empresas</div>
        <div class="value">${d.companies_total ?? "—"}</div>
        <div class="hint">${d.companies_active_7d ?? 0} activas (7d) · ${d.companies_inactive ?? 0} sin uso 7d</div>
      </div>
      <div class="stat"><div class="label">Órdenes hoy</div><div class="value">${d.orders_today}</div></div>
      <div class="stat"><div class="label">Órdenes 7d</div><div class="value">${d.orders_week}</div></div>
      <div class="stat"><div class="label">Fallidas 7d</div><div class="value">${d.failed_week}</div></div>
      <div class="stat"><div class="label">Incidentes</div><div class="value">${d.open_incidents}</div></div>
      <div class="stat danger-stat"><div class="label">Críticos</div><div class="value">${d.critical_open}</div></div>
    </div>
    <h3 style="margin-top:1.25rem">Top licencias por uso</h3>
    <table>
      <thead><tr><th>Código</th><th>Label</th><th>Usos</th><th>Límite</th></tr></thead>
      <tbody>
        ${d.top_licenses_by_use
          .map(
            (l) =>
              `<tr><td class="mono">${escape(l.code)}</td><td>${escape(l.label)}</td><td>${l.uses}</td><td>${
                l.limit || "∞"
              }</td></tr>`
          )
          .join("")}
      </tbody>
    </table>
    <p class="hint" style="margin-top:1rem">Detalle por cliente en la pestaña <strong>Uso empresas</strong>.</p>
  `;
}

async function loadCompanyUsage() {
  const data = await API.request("/api/admin/monitoring/companies");
  const s = data.summary || {};
  $("#usage-summary").innerHTML = `
    <div class="stat"><div class="label">Total empresas</div><div class="value">${s.total || 0}</div></div>
    <div class="stat"><div class="label">Activas 7d</div><div class="value">${s.active || 0}</div>
      <div class="hint">Procesaron al menos 1 orden esta semana</div></div>
    <div class="stat warn-stat"><div class="label">Uso reciente 30d</div><div class="value">${s.warm || 0}</div></div>
    <div class="stat"><div class="label">Inactivas +30d</div><div class="value">${s.dormant || 0}</div></div>
    <div class="stat danger-stat"><div class="label">Sin uso jamás</div><div class="value">${s.never || 0}</div>
      <div class="hint">Con cuenta pero sin procesar</div></div>
  `;
  const items = data.items || [];
  if (!items.length) {
    $("#usage-body").innerHTML = `<tr><td colspan="11" class="muted">Sin empresas registradas.</td></tr>`;
    return;
  }
  $("#usage-body").innerHTML = items
    .map((r) => {
      const hc = `health-${r.health || "never"}`;
      return `<tr>
        <td><strong>${escape(r.company_name)}</strong></td>
        <td class="mono">${escape(r.client_code || "—")}</td>
        <td class="${hc}">${escape(r.health_label || r.health)}</td>
        <td>${r.active_users || 0}/${r.users || 0}</td>
        <td>${r.orders_today || 0}</td>
        <td><strong>${r.orders_7d || 0}</strong></td>
        <td>${r.orders_30d || 0}</td>
        <td>${r.orders_total || 0}</td>
        <td>${r.license_uses || 0}</td>
        <td class="muted">${fmtDate(r.last_order_at)}</td>
        <td class="muted">${fmtDate(r.last_login)}</td>
      </tr>`;
    })
    .join("");
}

async function loadLicenses() {
  const list = await API.request("/api/admin/licenses");
  $("#licenses-body").innerHTML = list
    .map((l) => {
      const limit = l.limit_uses > 0 ? l.limit_uses : "∞";
      const daily = l.daily_limit > 0 ? l.daily_limit : "∞";
      return `<tr>
        <td class="mono">${escape(l.code)}</td>
        <td>${escape(l.label)} <span class="badge badge-muted">${escape(l.type)}</span></td>
        <td>${escape(l.company_name || "—")}</td>
        <td>${l.uses}/${limit} · hoy ${l.uses_today}/${daily}</td>
        <td>${l.expiry || "—"} (${l.days_left == null ? "∞" : l.days_left + "d"})</td>
        <td><span class="badge ${l.active ? "badge-ok" : "badge-err"}">${l.active ? "activa" : "off"}</span></td>
        <td class="row-actions">
          <button class="btn btn-sm btn-ghost" type="button" data-action="toggle-lic" data-id="${escape(l.id)}">Toggle</button>
          <button class="btn btn-sm btn-ghost" type="button" data-action="reset-uses" data-id="${escape(l.id)}">Reset usos</button>
          <button class="btn btn-sm btn-ghost" type="button" data-action="renew-lic" data-id="${escape(l.id)}">+30d</button>
        </td>
      </tr>`;
    })
    .join("");
}

async function loadUsers() {
  const users = await API.request("/api/admin/users");
  $("#users-body").innerHTML = users
    .map(
      (u) => `<tr>
      <td>${escape(u.email)}</td>
      <td>${escape(u.full_name)}</td>
      <td><span class="badge badge-muted">${escape(u.role)}</span></td>
      <td class="mono">${escape(u.client_code)}</td>
      <td>${escape(u.company_name || "—")}</td>
      <td><span class="badge ${u.is_active ? "badge-ok" : "badge-err"}">${u.is_active ? "activo" : "off"}</span></td>
      <td><button class="btn btn-sm btn-ghost" type="button" data-action="toggle-user" data-id="${escape(u.id)}">Toggle</button></td>
    </tr>`
    )
    .join("");
}

async function loadLogs() {
  const logs = await API.request("/api/admin/monitoring/logs?limit=80");
  $("#logs-body").innerHTML = logs
    .map(
      (r) => `<tr>
      <td class="muted">${r.created_at ? new Date(r.created_at).toLocaleString() : ""}</td>
      <td><span class="badge badge-muted">${escape(r.event_type)}</span></td>
      <td class="mono">${escape(r.license_code)}</td>
      <td>${escape(r.detail)}</td>
      <td class="mono">${escape(r.ip)}</td>
    </tr>`
    )
    .join("");
}

async function loadIncidents() {
  const rows = await API.request("/api/admin/monitoring/incidents?limit=80");
  $("#incidents-body").innerHTML = rows
    .map(
      (r) => `<tr>
      <td class="severity-${r.severity}">${escape(r.severity)}</td>
      <td>${escape(r.category)}</td>
      <td><strong>${escape(r.title)}</strong><div class="muted">${escape(r.detail)}</div></td>
      <td class="mono">${escape(r.license_code)}</td>
      <td class="mono">${escape(r.ip)}</td>
      <td>${
        r.resolved
          ? "✓"
          : `<button class="btn btn-sm btn-primary" type="button" data-action="resolve-inc" data-id="${r.id}">Resolver</button>`
      }</td>
    </tr>`
    )
    .join("");
}

function applyTemplateHints() {
  const t = $("#template").value;
  const hints = {
    trial: "50 órdenes globales · 3/día · 7 días",
    standard: "500 órdenes · 50/día · 30 días",
    pro: "Ilimitado global · 200/día · 365 días",
    enterprise: "Ilimitado · sin tope diario · 365 días",
    custom: "Configura los campos manualmente",
  };
  $("#template-hint").textContent = hints[t] || "";
}

async function createLicense(e) {
  e.preventDefault();
  const body = {
    code: $("#lic-code").value.trim() || null,
    label: $("#lic-label").value.trim(),
    company_name: $("#lic-company").value.trim(),
    template: $("#template").value || null,
    max_devices: numOrNull($("#lic-devices").value),
    limit_uses: numOrNull($("#lic-limit").value),
    daily_limit: numOrNull($("#lic-daily").value),
    duration_days: numOrNull($("#lic-days").value),
    count_toward_global: $("#lic-count-global").checked,
    enforce_daily_limit: $("#lic-enforce-daily").checked,
    notes: $("#lic-notes").value.trim(),
  };
  if (body.template === "custom") body.template = null;
  Object.keys(body).forEach((k) => {
    if (body[k] === null || body[k] === "") delete body[k];
  });
  try {
    await API.request("/api/admin/licenses", { method: "POST", body: JSON.stringify(body) });
    $("#form-license").reset();
    $("#lic-count-global").checked = true;
    $("#lic-enforce-daily").checked = true;
    await loadLicenses();
    await loadOverview();
    showAlert($("#admin-alert"), "Licencia creada.", "ok");
  } catch (err) {
    showAlert($("#admin-alert"), err.message);
  }
}

function numOrNull(v) {
  if (v === "" || v == null) return null;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
}

async function toggleLic(id) {
  await API.request(`/api/admin/licenses/${id}/toggle`, { method: "POST" });
  await loadLicenses();
}
async function resetUses(id) {
  await API.request(`/api/admin/licenses/${id}/reset-uses`, { method: "POST" });
  await loadLicenses();
}
async function renewLic(id) {
  await API.request(`/api/admin/licenses/${id}/renew?days=30`, { method: "POST" });
  await loadLicenses();
}
async function toggleUser(id) {
  await API.request(`/api/admin/users/${id}/toggle`, { method: "POST" });
  await loadUsers();
}
async function resolveInc(id) {
  await API.request(`/api/admin/monitoring/incidents/${id}/resolve`, { method: "POST" });
  await loadIncidents();
  await loadOverview();
}

function escape(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Delegación de eventos: sin onclick inline (CSP script-src 'self'). */
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  const id = btn.dataset.id;
  if (!action) return;

  if (action === "toggle-lic") {
    e.preventDefault();
    toggleLic(id);
  } else if (action === "reset-uses") {
    e.preventDefault();
    resetUses(id);
  } else if (action === "renew-lic") {
    e.preventDefault();
    renewLic(id);
  } else if (action === "toggle-user") {
    e.preventDefault();
    toggleUser(id);
  } else if (action === "resolve-inc") {
    e.preventDefault();
    resolveInc(id);
  }
});

init();
