const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const titles = {
  dashboard: { title: "Inicio", sub: "Estadísticas de uso de tu plan" },
  upload: { title: "Cargar orden", sub: "Procesa un Excel y descarga el resultado" },
  history: { title: "Histórico", sub: "Órdenes subidas y archivos generados" },
  team: { title: "Equipo", sub: "Colaboradores y firmas de consentimiento" },
};

let currentUser = null;
let selectedFile = null;
let pendingDoc = null;

function setLoggedIn(yes) {
  $("#auth-view").classList.toggle("hidden", yes);
  $("#app-view").classList.toggle("hidden", !yes);
}

function showAlert(el, message, type = "error") {
  if (!el) return;
  el.className = `alert alert-${type === "ok" ? "ok" : type === "info" ? "info" : "error"}`;
  el.textContent = message;
  el.classList.remove("hidden");
}

function hideAlert(el) {
  if (el) el.classList.add("hidden");
}

function showView(name) {
  $$(".view").forEach((v) => v.classList.add("hidden"));
  const el = $(`#view-${name}`);
  if (el) el.classList.remove("hidden");
  $$(".nav-item[data-view]").forEach((n) => {
    n.classList.toggle("active", n.dataset.view === name);
  });
  const t = titles[name] || titles.dashboard;
  $("#page-title").textContent = t.title;
  $("#page-sub").textContent = t.sub;
  hideAlert($("#global-alert"));
  if (name === "dashboard") loadDashboard();
  if (name === "history") loadHistory();
  if (name === "team") loadTeam();
}

function simpleMarkdown(md) {
  return String(md || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^### (.*)$/gm, "<h2>$1</h2>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>")
    .replace(/^# (.*)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/^- (.*)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`)
    .replace(/\n{2,}/g, "<br/><br/>")
    .replace(/\n/g, "<br/>");
}

async function ensureConsent() {
  try {
    const p = await API.legalPending();
    if (p.required && p.document) {
      pendingDoc = p.document;
      $("#legal-title").textContent = p.document.title || "Consentimiento legal";
      $("#legal-body").innerHTML = simpleMarkdown(p.document.body);
      $("#legal-overlay").classList.remove("hidden");
      return false;
    }
  } catch {
    /* si falla, no bloquear platform-like errors silently for employees */
  }
  $("#legal-overlay").classList.add("hidden");
  return true;
}

async function onLegalSign() {
  hideAlert($("#legal-alert"));
  if (!$("#legal-accept").checked) {
    showAlert($("#legal-alert"), "Debes marcar la casilla de aceptación.");
    return;
  }
  const name = $("#legal-signature").value.trim();
  if (name.length < 3) {
    showAlert($("#legal-alert"), "Escribe tu nombre completo como firma.");
    return;
  }
  if (!pendingDoc) {
    showAlert($("#legal-alert"), "No hay documento pendiente.");
    return;
  }
  try {
    await API.legalSign({
      document_id: pendingDoc.id,
      signature_name: name,
      accepted: true,
    });
    $("#legal-overlay").classList.add("hidden");
    currentUser = await API.me();
    renderUser();
    showView("dashboard");
  } catch (err) {
    showAlert($("#legal-alert"), err.message);
  }
}

async function enterApp() {
  setLoggedIn(true);
  renderUser();
  const ok = await ensureConsent();
  if (ok) showView("dashboard");
}

async function init() {
  $("#tab-login").addEventListener("click", () => {
    $("#tab-login").classList.add("active");
    $("#tab-register").classList.remove("active");
    $("#login-form").classList.remove("hidden");
    $("#register-form").classList.add("hidden");
    hideAlert($("#auth-alert"));
  });
  $("#tab-register").addEventListener("click", () => {
    $("#tab-register").classList.add("active");
    $("#tab-login").classList.remove("active");
    $("#register-form").classList.remove("hidden");
    $("#login-form").classList.add("hidden");
    hideAlert($("#auth-alert"));
  });

  $("#login-form").addEventListener("submit", onLogin);
  $("#register-form").addEventListener("submit", onRegister);
  $("#btn-logout").addEventListener("click", onLogout);
  $("#btn-legal-sign").addEventListener("click", onLegalSign);
  $("#legal-signature").addEventListener("input", (e) => {
    $("#signature-preview").textContent = e.target.value.trim() || "—";
  });

  $$(".nav-item[data-view]").forEach((n) => {
    n.addEventListener("click", () => showView(n.dataset.view));
  });
  $$("[data-go]").forEach((b) => {
    b.addEventListener("click", () => showView(b.dataset.go));
  });

  const drop = $("#dropzone");
  const fileInput = $("#file-input");
  drop.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => pickFile(e.target.files[0]));
  ;["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add("dragover");
    })
  );
  ;["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.remove("dragover");
    })
  );
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f) pickFile(f);
  });
  $("#btn-process").addEventListener("click", onProcess);

  const formEmp = $("#form-employee");
  if (formEmp) formEmp.addEventListener("submit", onCreateEmployee);

  currentUser = await loadSession();
  if (currentUser) {
    if (currentUser.role === "admin") {
      // Platform admin no usa portal empresas
      API.clearTokens();
      setLoggedIn(false);
      return;
    }
    await enterApp();
  } else {
    setLoggedIn(false);
  }
}

function renderUser() {
  $("#user-name").textContent = currentUser.full_name || currentUser.email;
  $("#user-meta").textContent = [
    currentUser.company_name || currentUser.client_code || "Empresa",
    currentUser.role === "company_admin" ? "Admin empresa" : "Colaborador",
  ].join(" · ");
  const teamNav = $("#nav-team");
  if (teamNav) {
    teamNav.classList.toggle("hidden", currentUser.role !== "company_admin");
  }
}

async function onLogin(e) {
  e.preventDefault();
  hideAlert($("#auth-alert"));
  try {
    const tokens = await API.login($("#email").value.trim(), $("#password").value);
    API.setTokens(tokens.access_token, tokens.refresh_token);
    currentUser = await API.me();
    await enterApp();
  } catch (err) {
    showAlert($("#auth-alert"), err.message);
  }
}

async function onRegister(e) {
  e.preventDefault();
  hideAlert($("#auth-alert"));
  try {
    const tokens = await API.register({
      email: $("#reg-email").value.trim(),
      password: $("#reg-password").value,
      full_name: $("#reg-name").value.trim(),
      license_code: $("#reg-license").value.trim(),
      as_company_admin: true,
    });
    API.setTokens(tokens.access_token, tokens.refresh_token);
    currentUser = await API.me();
    await enterApp();
  } catch (err) {
    showAlert($("#auth-alert"), err.message);
  }
}

async function onLogout() {
  try {
    await API.logout();
  } catch {
    API.clearTokens();
  }
  currentUser = null;
  setLoggedIn(false);
  $("#legal-overlay").classList.add("hidden");
}

function pickFile(file) {
  if (!file) return;
  selectedFile = file;
  $("#file-name").textContent = file.name;
  $("#btn-process").disabled = false;
  $("#upload-hint").textContent = "Listo para procesar";
}

async function onProcess() {
  if (!selectedFile) return;
  hideAlert($("#global-alert"));
  const btn = $("#btn-process");
  btn.disabled = true;
  btn.textContent = "Procesando…";
  try {
    const res = await API.processFile(selectedFile);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = selectedFile.name.replace(/\.xlsx?$/i, "") + "_FulfillPro.xlsx";
    a.click();
    URL.revokeObjectURL(a.href);
    showAlert(
      $("#global-alert"),
      "Orden procesada. El Excel incluye el distintivo de tu empresa.",
      "ok"
    );
    selectedFile = null;
    $("#file-name").textContent = "Ningún archivo seleccionado";
    $("#upload-hint").textContent = "";
  } catch (err) {
    showAlert($("#global-alert"), err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Procesar y descargar";
  }
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function progressClass(pct) {
  if (pct >= 90) return "danger";
  if (pct >= 75) return "warn";
  return "";
}

function statClass(pct) {
  if (pct >= 90) return "danger-stat";
  if (pct >= 75) return "warn-stat";
  return "";
}

async function loadDashboard() {
  const box = $("#dash-stats");
  const warnBox = $("#dash-warnings");
  const recent = $("#dash-recent");
  const banner = $("#company-banner");
  try {
    const d = await API.dashboard();
    if (!d.ok || !d.license) {
      box.innerHTML = `<div class="empty-state"><div class="big">◇</div><p>${escapeHtml(
        d.message || "Sin licencia asignada a tu empresa."
      )}</p></div>`;
      warnBox.innerHTML = "";
      recent.innerHTML = "—";
      banner.classList.add("hidden");
      return;
    }
    const L = d.license;
    const brand = d.brand || {};
    $("#company-banner-text").textContent =
      brand.brand_line || `Empresa: ${brand.company_name || "—"} · Lic. ${L.code}`;
    banner.classList.remove("hidden");
    $("#top-company").textContent = brand.company_name || L.company_name || "";

    const limit = L.limit_uses > 0 ? L.limit_uses : null;
    const daily = L.daily_limit > 0 ? L.daily_limit : null;
    const days = L.days_left == null ? "∞" : L.days_left;
    const pct = L.usage_percent || 0;
    const pctD = L.daily_percent || 0;
    const remain = L.remaining_uses == null ? "∞" : L.remaining_uses;

    box.innerHTML = `
      <div class="stat ${statClass(pct)}">
        <div class="label">Usos del plan</div>
        <div class="value">${L.uses}${limit != null ? " / " + limit : ""}</div>
        <div class="hint">${limit != null ? remain + " restantes" : "Cupo ilimitado"}</div>
        ${
          limit != null
            ? `<div class="progress ${progressClass(pct)}"><span style="width:${Math.min(pct, 100)}%"></span></div>`
            : ""
        }
      </div>
      <div class="stat ${statClass(pctD)}">
        <div class="label">Uso hoy</div>
        <div class="value">${L.uses_today}${daily != null ? " / " + daily : ""}</div>
        <div class="hint">${daily != null ? "Límite diario" : "Sin tope diario"}</div>
        ${
          daily != null
            ? `<div class="progress ${progressClass(pctD)}"><span style="width:${Math.min(pctD, 100)}%"></span></div>`
            : ""
        }
      </div>
      <div class="stat ${L.days_left != null && L.days_left <= 3 ? "warn-stat" : ""}">
        <div class="label">Días restantes</div>
        <div class="value">${days}</div>
        <div class="hint">Vence: ${L.expiry || "sin fecha"}</div>
      </div>
      <div class="stat">
        <div class="label">Órdenes empresa</div>
        <div class="value">${d.orders_total}</div>
        <div class="hint">${d.orders_week} esta semana · ${d.orders_completed} ok</div>
      </div>
      <div class="stat">
        <div class="label">Plan</div>
        <div class="value" style="font-size:1.1rem">${escapeHtml(L.type || "—")}</div>
        <div class="hint mono">${escapeHtml(L.code)}</div>
      </div>
    `;

    const warnings = L.warnings || [];
    warnBox.innerHTML = warnings.length
      ? warnings.map((w) => `<div class="warning-item"><span>⚠</span><span>${escapeHtml(w)}</span></div>`).join("")
      : `<div class="hint" style="margin-top:0.85rem">Tu plan está en buen estado. Sin alertas de cupo.</div>`;

    const hist = await API.listOrders(1);
    if (!hist.items.length) {
      recent.innerHTML = `<div class="empty-state"><div class="big">⬚</div><p>Aún no hay órdenes. Ve a <strong>Cargar orden</strong>.</p></div>`;
    } else {
      recent.innerHTML = `<table>
        <thead><tr><th>Archivo</th><th>Estado</th><th>Fecha</th></tr></thead>
        <tbody>
          ${hist.items
            .slice(0, 5)
            .map((o) => {
              const badge =
                o.status === "completed" ? "badge-ok" : o.status === "failed" ? "badge-err" : "badge-muted";
              const when = o.created_at ? new Date(o.created_at).toLocaleString() : "—";
              return `<tr>
                <td>${escapeHtml(o.original_filename)}</td>
                <td><span class="badge ${badge}">${o.status}</span></td>
                <td class="muted">${when}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
    }
  } catch (err) {
    box.innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
  }
}

async function loadHistory() {
  const body = $("#orders-body");
  try {
    const data = await API.listOrders(1);
    if (!data.items.length) {
      body.innerHTML = `<tr><td colspan="7"><div class="empty-state">Sin órdenes en el histórico.</div></td></tr>`;
      return;
    }
    body.innerHTML = data.items
      .map((o) => {
        const badge =
          o.status === "completed" ? "badge-ok" : o.status === "failed" ? "badge-err" : "badge-muted";
        const when = o.created_at ? new Date(o.created_at).toLocaleString() : "—";
        const co = (o.meta && o.meta.company_name) || o.client_code || "—";
        const dl =
          o.status === "completed"
            ? `<button class="btn btn-sm btn-ghost" type="button" onclick="dlAuth('${o.id}','output')">Salida</button>
               <button class="btn btn-sm btn-ghost" type="button" onclick="dlAuth('${o.id}','input')">Entrada</button>`
            : "—";
        return `<tr>
          <td class="mono">${String(o.id).slice(0, 8)}…</td>
          <td>${escapeHtml(o.original_filename)}</td>
          <td><span class="badge ${badge}">${o.status}</span></td>
          <td>${o.row_count || 0} / ${o.priority_count || 0}</td>
          <td>${escapeHtml(co)}</td>
          <td class="muted">${when}</td>
          <td class="row-actions">${dl}</td>
        </tr>`;
      })
      .join("");
  } catch (err) {
    body.innerHTML = `<tr><td colspan="7">${escapeHtml(err.message)}</td></tr>`;
  }
}

async function loadTeam() {
  const body = $("#team-body");
  try {
    const list = await API.companyEmployees();
    if (!list.length) {
      body.innerHTML = `<tr><td colspan="6" class="muted">Aún no hay colaboradores.</td></tr>`;
      return;
    }
    body.innerHTML = list
      .map((u) => {
        const terms = u.terms_accepted_at
          ? `<span class="badge badge-ok">Firmado</span>`
          : `<span class="badge badge-warn">Pendiente</span>`;
        return `<tr>
          <td>${escapeHtml(u.email)}</td>
          <td>${escapeHtml(u.full_name || "—")}</td>
          <td><span class="badge badge-muted">${escapeHtml(u.role)}</span></td>
          <td>${terms}</td>
          <td><span class="badge ${u.is_active ? "badge-ok" : "badge-err"}">${
            u.is_active ? "activo" : "off"
          }</span></td>
          <td><button class="btn btn-sm btn-ghost" type="button" onclick="toggleEmp('${u.id}')">Toggle</button></td>
        </tr>`;
      })
      .join("");
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6">${escapeHtml(err.message)}</td></tr>`;
  }
}

async function onCreateEmployee(e) {
  e.preventDefault();
  hideAlert($("#global-alert"));
  try {
    await API.createEmployee({
      email: $("#emp-email").value.trim(),
      password: $("#emp-pass").value,
      full_name: $("#emp-name").value.trim(),
    });
    showAlert(
      $("#global-alert"),
      "Usuario creado. En su primer login deberá firmar los términos legales.",
      "ok"
    );
    $("#form-employee").reset();
    await loadTeam();
  } catch (err) {
    showAlert($("#global-alert"), err.message);
  }
}

async function toggleEmp(id) {
  await API.toggleEmployee(id);
  await loadTeam();
}

async function dlAuth(orderId, kind) {
  try {
    const res = await API.request(`/api/orders/${orderId}/files/${kind}`);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = kind + ".xlsx";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    showAlert($("#global-alert"), err.message);
  }
}

window.dlAuth = dlAuth;
window.toggleEmp = toggleEmp;
init();
