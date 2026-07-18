function requireAuth() {
  if (!API.token) {
    window.location.href = "/";
    return false;
  }
  return true;
}

async function loadSession() {
  if (!API.token) return null;
  try {
    return await API.me();
  } catch {
    API.clearTokens();
    return null;
  }
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
