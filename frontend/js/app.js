const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const titles = {
  dashboard: { title: "Inicio", sub: "Estadísticas de uso de tu plan" },
  upload: { title: "Cargar orden", sub: "Procesa un Excel y descarga el resultado" },
  history: { title: "Histórico", sub: "Órdenes subidas y archivos generados" },
  team: { title: "Equipo", sub: "Colaboradores y firmas de consentimiento" },
  analytics: {
    title: "Más vendidos",
    sub: "Analítica semanal con deduplicación de órdenes",
  },
};

let currentUser = null;
let selectedFile = null;
let pendingDoc = null;
let chartUnits = null;
let chartLines = null;
let analyticsState = null;

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
  if (name === "analytics") loadAnalytics();
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

  const btnRefresh = $("#btn-analytics-refresh");
  if (btnRefresh) btnRefresh.addEventListener("click", () => loadAnalytics());
  const btnCons = $("#btn-consolidate");
  if (btnCons) btnCons.addEventListener("click", onConsolidate);
  const btnDlPdf = $("#btn-dl-pdf");
  const btnDlJson = $("#btn-dl-json");
  const btnCloseViewer = $("#btn-close-viewer");
  if (btnDlPdf)
    btnDlPdf.addEventListener("click", () => {
      const id = btnDlPdf.dataset.weekId;
      if (id) downloadConsolidation(id, "pdf");
    });
  if (btnDlJson)
    btnDlJson.addEventListener("click", () => {
      const id = btnDlJson.dataset.weekId;
      if (id) downloadConsolidation(id, "json");
    });
  if (btnCloseViewer)
    btnCloseViewer.addEventListener("click", () => {
      $("#consolidado-viewer").classList.add("hidden");
    });

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
  const anNav = $("#nav-analytics");
  if (anNav) {
    // Admin empresa ve consolidado; empleados pueden ver tiempo real
    anNav.classList.toggle(
      "hidden",
      !["company_admin", "employee", "client"].includes(currentUser.role)
    );
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

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function setProcessProgress(pct, stage) {
  const p = Math.max(0, Math.min(100, Math.round(pct)));
  const fill = $("#process-bar-fill");
  const label = $("#process-pct");
  const stageEl = $("#process-stage");
  if (fill) fill.style.width = p + "%";
  if (label) label.textContent = p + "%";
  if (stageEl && stage) stageEl.textContent = stage;
}

function showProcessOverlay(show) {
  $("#process-overlay").classList.toggle("hidden", !show);
  if (show) setProcessProgress(0, "Preparando…");
}

function showToast(title, message, type = "warn", duration = 6500) {
  const stack = $("#toast-stack");
  if (!stack) return;
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  const ico = type === "ok" ? "✓" : type === "danger" ? "!" : type === "info" ? "ℹ" : "⚠";
  el.className = `toast toast-${type === "info" ? "ok" : type}`;
  el.innerHTML = `<div class="toast-ico">${ico}</div><div><strong>${escapeHtml(
    title
  )}</strong><p>${escapeHtml(message)}</p></div>`;
  stack.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity .3s";
    setTimeout(() => el.remove(), 320);
  }, duration);
}

async function onProcess() {
  if (!selectedFile) return;
  hideAlert($("#global-alert"));
  const btn = $("#btn-process");
  btn.disabled = true;
  btn.textContent = "Encolando…";
  const originalName = selectedFile.name;

  showProcessOverlay(true);
  setProcessProgress(5, "Subiendo y encolando…");

  try {
    // 1) Encolar (202 JSON) — la API no procesa Excel; workers acotados lo hacen
    const enqueued = await API.processFile(selectedFile);
    const orderId = enqueued.order_id || enqueued.job_id;
    if (!orderId) throw new Error("No se recibió order_id del servidor.");

    const qPos = enqueued.queue_position || enqueued.queue_depth || "?";
    setProcessProgress(12, `En cola (posición ~${qPos})…`);
    showToast(
      "Trabajo encolado",
      `Tu archivo está en la cola (pos. ${qPos}). La plataforma acepta 100+ envíos a la vez; los workers procesan en paralelo de forma estable.`,
      "info",
      5500
    );

    // 2) Polling adaptativo (rápido al inicio, más suave con cola profunda)
    let status = "queued";
    let priorityCount = 0;
    let totalRisk = 0;
    let rowCount = 0;
    let lastError = "";
    const started = Date.now();
    const maxWaitMs = 30 * 60 * 1000; // 30 min
    let pollMs = 800;
    let ticks = 0;

    while (Date.now() - started < maxWaitMs) {
      await sleep(pollMs);
      ticks += 1;
      const st = await API.jobStatus(orderId);
      status = st.status;
      priorityCount = st.priority_count || 0;
      totalRisk = st.total_risk || 0;
      rowCount = st.row_count || 0;
      lastError = st.error || "";
      const depth = st.queue_depth ?? 0;
      const prog = Math.max(12, Math.min(90, st.progress || 15));
      const stage =
        st.stage ||
        (status === "queued"
          ? `En cola (profundidad ${depth})…`
          : status === "processing"
          ? "Procesando Excel en worker…"
          : status);
      setProcessProgress(prog, stage);

      // Polling más lento si hay mucha cola (menos carga en API con 100+ clientes)
      if (status === "queued" && depth > 20) pollMs = Math.min(3000, 800 + Math.floor(depth * 20));
      else if (status === "processing") pollMs = 1000;
      else pollMs = 800;

      if (status === "completed" || status === "failed") break;
    }

    if (status === "failed") {
      throw new Error(lastError || "El procesamiento falló. Revisa el archivo o reintenta.");
    }
    if (status !== "completed") {
      throw new Error(
        "Tiempo de espera agotado. El job sigue en cola o procesándose; revisa el histórico más tarde."
      );
    }

    setProcessProgress(92, "Analizando prioritarias…");
    await sleep(400);

    if (priorityCount > 0) {
      showToast(
        `Tienes ${priorityCount} orden${priorityCount === 1 ? "" : "es"} en riesgo`,
        `Hoja PRIORITARIAS · Riesgo 20%: $${Number(totalRisk).toLocaleString("es-CO")}.`,
        priorityCount >= 5 ? "danger" : "warn",
        8000
      );
    } else {
      showToast("Sin órdenes en riesgo", "No hay guías atrasadas en PRIORITARIAS.", "ok", 4000);
    }

    setProcessProgress(96, "Descargando resultado…");
    const res = await API.jobDownload(orderId);
    const blob = await res.blob();
    setProcessProgress(100, "¡Listo!");

    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = originalName.replace(/\.xlsx?$/i, "") + "_FulfillPro.xlsx";
    a.click();
    URL.revokeObjectURL(a.href);

    await sleep(350);
    showProcessOverlay(false);

    showAlert(
      $("#global-alert"),
      `Procesado en cola: ${rowCount} filas · ${priorityCount} en riesgo. Archivo descargado.`,
      "ok"
    );
    selectedFile = null;
    $("#file-name").textContent = "Ningún archivo seleccionado";
    $("#upload-hint").textContent = "";
  } catch (err) {
    showProcessOverlay(false);
    showAlert($("#global-alert"), err.message);
    showToast("Error al procesar", err.message, "danger", 6000);
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

function destroyCharts() {
  if (chartUnits) {
    chartUnits.destroy();
    chartUnits = null;
  }
  if (chartLines) {
    chartLines.destroy();
    chartLines = null;
  }
}

function renderAnalyticsCharts(chart) {
  destroyCharts();
  if (!chart || !window.Chart) return;
  const labels = chart.labels || [];
  const units = chart.units || [];
  const lines = chart.lines || [];
  const colors = labels.map(
    (_, i) => `hsl(${(140 + i * 28) % 360} 55% ${42 + (i % 3) * 6}%)`
  );

  const ctxU = $("#chart-units");
  const ctxL = $("#chart-lines");
  if (ctxU) {
    chartUnits = new Chart(ctxU, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Unidades vendidas",
            data: units,
            backgroundColor: colors,
            borderRadius: 8,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { maxRotation: 45, minRotation: 0, font: { size: 10 } } },
          y: { beginAtZero: true },
        },
      },
    });
  }
  if (ctxL) {
    chartLines = new Chart(ctxL, {
      type: "doughnut",
      data: {
        labels,
        datasets: [
          {
            label: "Líneas",
            data: lines,
            backgroundColor: colors,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 10 } } } },
      },
    });
  }
}

function fmtCountdown(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  return `${h}h ${m}m`;
}

async function loadAnalytics() {
  try {
    const data = await API.analyticsCurrent();
    analyticsState = data;
    const meta = $("#analytics-week-meta");
    const banner = $("#analytics-banner");
    const btnCons = $("#btn-consolidate");
    const hint = $("#consolidate-hint");
    const rank = $("#analytics-rank-body");
    const weeksBody = $("#analytics-weeks-body");
    const storageEl = $("#analytics-storage");

    if (data.storage) {
      storageEl.textContent = `Almacenamiento analítica: ${data.storage.used_mb} MB / ${data.storage.limit_mb} MB · Retención: ${
        (data.limits && data.limits.weeks_retention) || "—"
      } semanas · Máx. eventos/semana: ${
        (data.limits && data.limits.max_events_per_week) || "—"
      }`;
    }

    if (!data.week) {
      meta.innerHTML = `<div class="stat"><div class="label">Estado</div><div class="value" style="font-size:1rem">Sin semana activa</div>
        <div class="hint">Sube un Excel para iniciar el ciclo de 7 días</div></div>`;
      banner.classList.add("hidden");
      if (btnCons) btnCons.classList.add("hidden");
      if (hint) hint.textContent = "";
      destroyCharts();
      rank.innerHTML = `<tr><td colspan="5" class="muted">${escapeHtml(
        data.message || "Sin datos"
      )}</td></tr>`;
    } else {
      const w = data.week;
      const statusLabel =
        w.status === "open"
          ? "En curso"
          : w.status === "ended"
          ? "Lista para consolidar"
          : w.status === "consolidated"
          ? "Consolidada"
          : w.status;
      meta.innerHTML = `
        <div class="stat"><div class="label">Estado</div><div class="value" style="font-size:1.05rem">${escapeHtml(
          statusLabel
        )}</div>
          <div class="hint">Día ${w.days_elapsed || 0} de ${w.days_total || 7}</div></div>
        <div class="stat"><div class="label">Tiempo restante</div><div class="value" style="font-size:1.15rem">${fmtCountdown(
          w.seconds_remaining
        )}</div>
          <div class="hint">Inicio: ${w.started_at ? new Date(w.started_at).toLocaleString() : "—"}</div></div>
        <div class="stat"><div class="label">Unidades únicas</div><div class="value">${
          w.total_units || 0
        }</div>
          <div class="hint">${w.events_count || 0} líneas sin duplicar</div></div>
        <div class="stat"><div class="label">Archivos del ciclo</div><div class="value">${
          w.files_ingested || 0
        }</div>
          <div class="hint">${w.unique_orders || 0} guías distintas</div></div>
      `;

      const earlyBox = $("#early-warning-box");
      if (w.status === "open" && w.is_early) {
        banner.classList.remove("hidden");
        banner.innerHTML = `<span>📊</span><span>Gráficos en <strong>tiempo real</strong> (día ${
          w.days_elapsed
        } de ${w.days_total}). Puedes <strong>forzar el consolidado</strong> antes de tiempo si lo necesitas.</span>`;
      } else if (w.period_complete && w.status !== "consolidated") {
        banner.classList.remove("hidden");
        banner.innerHTML = `<span>✅</span><span>La semana de ${w.days_total} días terminó. Genera el <strong>consolidado</strong> formal con ranking y gráficas.</span>`;
      } else if (w.status === "consolidated") {
        banner.classList.remove("hidden");
        banner.innerHTML = `<span>📁</span><span>Semana consolidada. El contador se reiniciará al subir un nuevo Excel.</span>`;
      } else {
        banner.classList.add("hidden");
      }

      const isAdmin = currentUser && ["company_admin", "admin"].includes(currentUser.role);
      if (btnCons) {
        const showBtn = isAdmin && w.can_consolidate && w.status !== "consolidated";
        btnCons.classList.toggle("hidden", !showBtn);
        btnCons.dataset.weekId = w.id;
        btnCons.dataset.early = w.is_early ? "1" : "0";
        btnCons.dataset.daysElapsed = String(w.days_elapsed || 0);
        btnCons.dataset.daysTotal = String(w.days_total || 7);
        if (w.is_early) {
          btnCons.textContent = "Forzar consolidado";
          btnCons.classList.remove("btn-primary");
          btnCons.classList.add("btn-ghost");
        } else {
          btnCons.textContent = "Generar consolidado";
          btnCons.classList.add("btn-primary");
          btnCons.classList.remove("btn-ghost");
        }
      }
      if (earlyBox) {
        if (isAdmin && w.is_early && w.status !== "consolidated") {
          earlyBox.classList.remove("hidden");
          earlyBox.innerHTML = `<span>⚠</span><span><strong>Consolidado temprano:</strong> el documento indicará que el periodo tiene solo <strong>${
            w.days_elapsed
          }</strong> día(s) de ${
            w.days_total
          }. Los datos no están pensados para mostrarse antes de tiempo y pueden generar incoherencias con análisis posteriores.</span>`;
        } else {
          earlyBox.classList.add("hidden");
        }
      }
      if (hint) {
        if (!isAdmin) hint.textContent = "Solo el admin de empresa puede generar el consolidado.";
        else if (w.status === "consolidated") hint.textContent = "Consolidado ya generado.";
        else if (w.is_early)
          hint.textContent = "Forzar cierra el ciclo ahora y archiva el ranking parcial.";
        else hint.textContent = "Cierra el ciclo completo de 7 días.";
      }

      renderAnalyticsCharts(w.chart);
      const tops = w.top_products || [];
      if (!tops.length) {
        rank.innerHTML = `<tr><td colspan="5" class="muted">Aún no hay productos en este ciclo.</td></tr>`;
      } else {
        rank.innerHTML = tops
          .map(
            (t, i) => `<tr>
            <td>${i + 1}</td>
            <td>${escapeHtml(t.product_name)}</td>
            <td>${escapeHtml(t.variation || "—")}</td>
            <td><strong>${t.units}</strong></td>
            <td>${t.lines}</td>
          </tr>`
          )
          .join("");
      }
    }

    const weeks = data.weeks || [];
    if (!weeks.length) {
      weeksBody.innerHTML = `<tr><td colspan="6" class="muted">Sin historial aún.</td></tr>`;
    } else {
      weeksBody.innerHTML = weeks
        .map((w) => {
          const st =
            w.status === "open"
              ? "badge-ok"
              : w.status === "consolidated"
              ? "badge-muted"
              : "badge-warn";
          const earlyBadge = w.early_consolidation
            ? ` <span class="badge badge-warn">temprano</span>`
            : "";
          const dl =
            w.has_consolidation || w.status === "consolidated"
              ? `<button class="btn btn-sm btn-primary" type="button" onclick="downloadConsolidation('${w.id}','pdf')">PDF</button>
                 <button class="btn btn-sm btn-ghost" type="button" onclick="openConsolidadoViewer('${w.id}')">Ver</button>`
              : `<button class="btn btn-sm btn-ghost" type="button" onclick="viewWeek('${w.id}')">Ver ranking</button>`;
          return `<tr>
            <td class="muted">${w.started_at ? new Date(w.started_at).toLocaleString() : "—"}</td>
            <td class="muted">${w.ends_at ? new Date(w.ends_at).toLocaleString() : "—"}</td>
            <td><span class="badge ${st}">${escapeHtml(w.status)}</span>${earlyBadge}</td>
            <td>${w.total_units || 0}</td>
            <td>${w.files_ingested || 0}</td>
            <td class="row-actions">${dl}</td>
          </tr>`;
        })
        .join("");
    }
  } catch (err) {
    showAlert($("#global-alert"), err.message);
  }
}

function triggerBlobDownload(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

async function downloadConsolidation(weekId, format = "pdf") {
  try {
    const res = await API.analyticsDownload(weekId, format);
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    let name = `Consolidado_${weekId.slice(0, 8)}.${format}`;
    const m = /filename="?([^";]+)"?/i.exec(cd);
    if (m) name = m[1];
    triggerBlobDownload(blob, name);
    showToast("Descarga lista", name, "ok", 3500);
  } catch (err) {
    showAlert($("#global-alert"), err.message);
    showToast("No se pudo descargar", err.message, "danger");
  }
}

function renderConsolidadoSummaryHtml(snapshot) {
  if (!snapshot) return "<p class='muted'>Sin datos de consolidado.</p>";
  const top5 = (snapshot.top5 || snapshot.top_products || []).slice(0, 5);
  const early = snapshot.early_consolidation;
  const days = snapshot.days_length != null ? snapshot.days_length : "—";
  const warn =
    early || (snapshot.warnings && snapshot.warnings.length)
      ? `<div class="c-warn"><strong>Advertencia</strong><br/>${escapeHtml(
          (snapshot.warnings && snapshot.warnings[0]) ||
            "Consolidado temprano: puede haber incoherencias con análisis posteriores."
        )}</div>`
      : "";
  const rows = top5
    .map(
      (t, i) => `<tr>
      <td>${i + 1}</td>
      <td>${escapeHtml(t.product_name || t.label || "")}</td>
      <td>${escapeHtml(t.variation || "—")}</td>
      <td><strong>${t.units || 0}</strong></td>
      <td>${t.lines || 0}</td>
    </tr>`
    )
    .join("");
  return `
    <p><strong>${escapeHtml(snapshot.company_name || "")}</strong> ·
    ${early ? "Consolidado temprano" : "Consolidado completo"} ·
    <strong>${days}</strong> día(s) de periodo</p>
    <p class="muted">Unidades: ${snapshot.total_units || 0} · Líneas: ${
    snapshot.unique_lines || 0
  } · Archivos: ${snapshot.files_ingested || 0}</p>
    ${warn}
    <p><strong>Top 5 en el PDF</strong> (tabla + gráficas de flujo diario y acumulado):</p>
    <table>
      <thead><tr><th>#</th><th>Producto</th><th>Variación</th><th>Unidades</th><th>Líneas</th></tr></thead>
      <tbody>${
        rows || "<tr><td colspan='5' class='muted'>Sin productos</td></tr>"
      }</tbody>
    </table>
    <p class="hint" style="margin-top:0.85rem">Descarga el <strong>PDF</strong> para ver las gráficas de flujo a lo largo de la semana.</p>
  `;
}

function showConsolidadoViewer(weekId, snapshot, metaLabel) {
  const viewer = $("#consolidado-viewer");
  const body = $("#consolidado-viewer-body");
  const meta = $("#consolidado-viewer-meta");
  if (!viewer || !body) return;
  body.innerHTML = renderConsolidadoSummaryHtml(snapshot);
  if (meta) meta.textContent = metaLabel || "";
  const btnP = $("#btn-dl-pdf");
  const btnJ = $("#btn-dl-json");
  if (btnP) btnP.dataset.weekId = weekId;
  if (btnJ) btnJ.dataset.weekId = weekId;
  viewer.classList.remove("hidden");
  viewer.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function openConsolidadoViewer(weekId) {
  try {
    const data = await API.analyticsWeek(weekId);
    const early = data.week && data.week.early_consolidation;
    const days = (data.snapshot && data.snapshot.days_length) || (data.week && data.week.days_length);
    showConsolidadoViewer(
      weekId,
      data.snapshot,
      early
        ? `Consolidado temprano · ${days != null ? days + " día(s)" : "parcial"} · PDF disponible`
        : `Consolidado · ${days != null ? days + " día(s)" : "semana completa"} · PDF disponible`
    );
    if (data.snapshot && data.snapshot.chart) {
      renderAnalyticsCharts(data.snapshot.chart);
    } else if (data.week && data.week.chart) {
      renderAnalyticsCharts(data.week.chart);
    }
    const tops =
      (data.snapshot && data.snapshot.top_products) ||
      (data.week && data.week.top_products) ||
      [];
    if (tops.length) {
      $("#analytics-rank-body").innerHTML = tops
        .map(
          (t, i) => `<tr>
          <td>${i + 1}</td>
          <td>${escapeHtml(t.product_name)}</td>
          <td>${escapeHtml(t.variation || "—")}</td>
          <td><strong>${t.units}</strong></td>
          <td>${t.lines}</td>
        </tr>`
        )
        .join("");
    }
  } catch (err) {
    showAlert($("#global-alert"), err.message);
  }
}

async function onConsolidate() {
  const btn = $("#btn-consolidate");
  const weekId = btn && btn.dataset.weekId;
  if (!weekId) return;

  const early = btn.dataset.early === "1";
  const daysElapsed = btn.dataset.daysElapsed || "0";
  const daysTotal = btn.dataset.daysTotal || "7";

  if (early) {
    const ok = confirm(
      "¿Forzar consolidado de forma anticipada?\n\n" +
        `• El periodo tendrá solo ${daysElapsed} día(s) de ${daysTotal} planificados.\n` +
        "• El documento marcará explícitamente que es un consolidado TEMPRANO.\n" +
        "• Los datos no están pensados para mostrarse antes de tiempo y pueden generar incoherencias con análisis posteriores.\n\n" +
        "Se descargará el archivo automáticamente y podrás verlo en pantalla."
    );
    if (!ok) return;
  } else {
    const ok = confirm(
      "¿Generar el consolidado de esta semana?\n\nSe descargará el archivo y se archivará el ranking. El contador se reiniciará con la próxima subida."
    );
    if (!ok) return;
  }

  btn.disabled = true;
  try {
    const res = await API.analyticsConsolidate(weekId, early);
    const days = res.days_length != null ? res.days_length : daysElapsed;

    // Descarga automática del PDF
    try {
      await downloadConsolidation(weekId, "pdf");
    } catch (_) {
      showToast(
        "PDF generado",
        "No se pudo auto-descargar; usa el botón Descargar PDF.",
        "warn",
        5000
      );
    }

    // Visor en pantalla (resumen + acceso al PDF)
    showConsolidadoViewer(
      weekId,
      res.snapshot,
      res.early
        ? `Consolidado temprano · ${days} día(s) de ${daysTotal} · PDF`
        : `Consolidado completo · ${days} día(s) · PDF`
    );

    if (res.early) {
      showToast(
        "PDF de consolidado temprano",
        `Periodo de ${days} día(s). Top 5 + gráficas de flujo en el PDF.`,
        "warn",
        8000
      );
      showAlert(
        $("#global-alert"),
        `Consolidado temprano en PDF · ${days} día(s). Tabla top 5 y gráficas incluidas.`,
        "ok"
      );
    } else {
      showToast(
        "PDF de consolidado listo",
        "Descargado. También está en el historial.",
        "ok",
        6000
      );
      showAlert($("#global-alert"), "Consolidado PDF generado y descargado.", "ok");
    }
    await loadAnalytics();
    if (res.snapshot && res.snapshot.chart) {
      renderAnalyticsCharts(res.snapshot.chart);
    }
  } catch (err) {
    showAlert($("#global-alert"), err.message);
    showToast("No se pudo consolidar", err.message, "danger");
  } finally {
    btn.disabled = false;
  }
}

async function viewWeek(id) {
  await openConsolidadoViewer(id);
}

window.viewWeek = viewWeek;
window.downloadConsolidation = downloadConsolidation;
window.openConsolidadoViewer = openConsolidadoViewer;

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
