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

  await Promise.all([loadOverview(), loadLicenses(), loadUsers(), loadLogs(), loadIncidents()]);
}

async function loadOverview() {
  const d = await API.request("/api/admin/monitoring/overview");
  $("#overview").innerHTML = `
    <div class="stats">
      <div class="stat"><div class="label">Usuarios</div><div class="value">${d.total_users}</div></div>
      <div class="stat"><div class="label">Licencias activas</div><div class="value">${d.active_licenses}</div></div>
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
  `;
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
          <button class="btn btn-sm btn-ghost" onclick="toggleLic('${l.id}')">Toggle</button>
          <button class="btn btn-sm btn-ghost" onclick="resetUses('${l.id}')">Reset usos</button>
          <button class="btn btn-sm btn-ghost" onclick="renewLic('${l.id}')">+30d</button>
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
      <td><button class="btn btn-sm btn-ghost" onclick="toggleUser('${u.id}')">Toggle</button></td>
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
          : `<button class="btn btn-sm btn-primary" onclick="resolveInc(${r.id})">Resolver</button>`
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

window.toggleLic = toggleLic;
window.resetUses = resetUses;
window.renewLic = renewLic;
window.toggleUser = toggleUser;
window.resolveInc = resolveInc;

init();
