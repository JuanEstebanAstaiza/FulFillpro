const $ = (s) => document.querySelector(s);

const ui = {
  loginView: $("#login-view"),
  appView: $("#app-view"),
  alert: $("#global-alert"),
  userLabel: $("#user-label"),
  licenseBox: $("#license-status"),
  ordersBody: $("#orders-body"),
  fileInput: $("#file-input"),
  dropzone: $("#dropzone"),
  processBtn: $("#btn-process"),
  activateForm: $("#activate-form"),
  deviceIdInput: $("#device-id"),
  licenseInput: $("#license-code"),
  countQuota: $("#count-quota"),
};

let currentUser = null;
let selectedFile = null;

function setView(loggedIn) {
  ui.loginView.classList.toggle("hidden", loggedIn);
  ui.appView.classList.toggle("hidden", !loggedIn);
}

async function init() {
  ui.deviceIdInput.value = API.deviceId;
  ui.licenseInput.value = API.licenseCode;

  $("#login-form").addEventListener("submit", onLogin);
  $("#btn-logout").addEventListener("click", onLogout);
  ui.activateForm.addEventListener("submit", onActivate);
  ui.processBtn.addEventListener("click", onProcess);
  ui.dropzone.addEventListener("click", () => ui.fileInput.click());
  ui.fileInput.addEventListener("change", (e) => pickFile(e.target.files[0]));
  ;["dragenter", "dragover"].forEach((ev) =>
    ui.dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      ui.dropzone.classList.add("dragover");
    })
  );
  ;["dragleave", "drop"].forEach((ev) =>
    ui.dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      ui.dropzone.classList.remove("dragover");
    })
  );
  ui.dropzone.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f) pickFile(f);
  });

  currentUser = await loadSession();
  if (currentUser) {
    setView(true);
    renderUser();
    await refreshLicense();
    await refreshOrders();
  } else {
    setView(false);
  }
}

function renderUser() {
  ui.userLabel.textContent = `${currentUser.full_name || currentUser.email} · ${currentUser.role}`;
  const adminLink = $("#admin-link");
  if (adminLink) adminLink.classList.toggle("hidden", currentUser.role !== "admin");
}

async function onLogin(e) {
  e.preventDefault();
  hideAlert(ui.alert);
  const email = $("#email").value.trim();
  const password = $("#password").value;
  try {
    const tokens = await API.login(email, password);
    API.setTokens(tokens.access_token, tokens.refresh_token);
    currentUser = await API.me();
    setView(true);
    renderUser();
    await refreshLicense();
    await refreshOrders();
  } catch (err) {
    showAlert(ui.alert, err.message);
  }
}

async function onLogout() {
  try {
    await API.logout();
  } catch {
    API.clearTokens();
  }
  currentUser = null;
  setView(false);
}

async function onActivate(e) {
  e.preventDefault();
  hideAlert(ui.alert);
  const code = ui.licenseInput.value.trim().toUpperCase();
  const deviceId = ui.deviceIdInput.value.trim().toUpperCase();
  const deviceName = $("#device-name").value.trim() || navigator.platform || "Equipo";
  try {
    API.setDeviceId(deviceId);
    API.setLicenseCode(code);
    const res = await API.activateLicense({
      code,
      device_id: deviceId,
      device_name: deviceName,
      device_fingerprint: API.deviceId,
      device_soft: API.deviceSoft,
    });
    showAlert(ui.alert, `Licencia activada (${res.device_status}).`, "ok");
    await refreshLicense();
  } catch (err) {
    showAlert(ui.alert, err.message);
  }
}

async function refreshLicense() {
  try {
    const st = await API.licenseStatus();
    if (!st.ok || !st.license) {
      ui.licenseBox.innerHTML = `<p class="muted">Sin licencia activa. Activa un código para procesar órdenes.</p>`;
      return;
    }
    const L = st.license;
    API.setLicenseCode(L.code);
    ui.licenseInput.value = L.code;
    const days = L.days_left == null ? "∞" : L.days_left;
    const limit = L.limit_uses > 0 ? L.limit_uses : "∞";
    const daily = L.daily_limit > 0 ? L.daily_limit : "∞";
    ui.licenseBox.innerHTML = `
      <div class="stats">
        <div class="stat"><div class="label">Licencia</div><div class="value" style="font-size:1rem">${L.code}</div></div>
        <div class="stat"><div class="label">Usos</div><div class="value">${L.uses}/${limit}</div></div>
        <div class="stat"><div class="label">Hoy</div><div class="value">${L.uses_today}/${daily}</div></div>
        <div class="stat"><div class="label">Equipos</div><div class="value">${L.devices_count}/${L.max_devices}</div></div>
        <div class="stat"><div class="label">Días rest.</div><div class="value">${days}</div></div>
      </div>
      <p class="muted">${L.label || L.type} · ${L.company_name || "—"} · ${L.active ? "Activa" : "Inactiva"}</p>
    `;
  } catch (err) {
    ui.licenseBox.innerHTML = `<p class="muted">${err.message}</p>`;
  }
}

function pickFile(file) {
  if (!file) return;
  selectedFile = file;
  $("#file-name").textContent = file.name;
  ui.processBtn.disabled = false;
}

async function onProcess() {
  if (!selectedFile) return;
  hideAlert(ui.alert);
  if (!API.licenseCode) {
    showAlert(ui.alert, "Activa una licencia antes de procesar.");
    return;
  }
  ui.processBtn.disabled = true;
  ui.processBtn.textContent = "Procesando…";
  try {
    const countQuota = ui.countQuota.checked;
    const res = await API.processFile(selectedFile, countQuota);
    const blob = await res.blob();
    const orderId = res.headers.get("X-Order-Id") || "";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = selectedFile.name.replace(/\.xlsx?$/i, "") + "_FulfillPro.xlsx";
    a.click();
    URL.revokeObjectURL(a.href);
    showAlert(ui.alert, `Procesado correctamente${orderId ? " · Orden " + orderId : ""}.`, "ok");
    selectedFile = null;
    $("#file-name").textContent = "Ningún archivo seleccionado";
    await refreshLicense();
    await refreshOrders();
  } catch (err) {
    showAlert(ui.alert, err.message);
  } finally {
    ui.processBtn.disabled = false;
    ui.processBtn.textContent = "Procesar y descargar";
  }
}

async function refreshOrders() {
  try {
    const data = await API.listOrders(1);
    if (!data.items.length) {
      ui.ordersBody.innerHTML = `<tr><td colspan="6" class="muted">Aún no hay órdenes.</td></tr>`;
      return;
    }
    ui.ordersBody.innerHTML = data.items
      .map((o) => {
        const badge =
          o.status === "completed"
            ? "badge-ok"
            : o.status === "failed"
            ? "badge-err"
            : "badge-muted";
        const when = o.created_at ? new Date(o.created_at).toLocaleString() : "—";
        const dl =
          o.status === "completed"
            ? `<a class="btn btn-sm btn-ghost" href="/api/orders/${o.id}/files/output" onclick="return dlAuth(event,'${o.id}','output')">Salida</a>
               <a class="btn btn-sm btn-ghost" href="#" onclick="return dlAuth(event,'${o.id}','input')">Entrada</a>`
            : "—";
        return `<tr>
          <td class="mono">${String(o.id).slice(0, 8)}…</td>
          <td>${escapeHtml(o.original_filename)}</td>
          <td><span class="badge ${badge}">${o.status}</span></td>
          <td>${o.row_count || 0} / ${o.priority_count || 0}</td>
          <td class="muted">${when}</td>
          <td class="row-actions">${dl}</td>
        </tr>`;
      })
      .join("");
  } catch (err) {
    ui.ordersBody.innerHTML = `<tr><td colspan="6">${escapeHtml(err.message)}</td></tr>`;
  }
}

async function dlAuth(e, orderId, kind) {
  e.preventDefault();
  try {
    const res = await API.request(`/api/orders/${orderId}/files/${kind}`);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = kind + ".xlsx";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    showAlert(ui.alert, err.message);
  }
  return false;
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

window.dlAuth = dlAuth;
init();
