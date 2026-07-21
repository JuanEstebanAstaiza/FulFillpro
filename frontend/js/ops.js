/** Login del portal Ops (/ops) — sin scripts inline (CSP script-src 'self'). */
document.getElementById("ops-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const alertEl = document.getElementById("ops-alert");
  if (alertEl) {
    alertEl.classList.add("hidden");
  }
  try {
    const tokens = await API.loginPlatform(
      document.getElementById("email").value.trim(),
      document.getElementById("password").value
    );
    API.setTokens(tokens.access_token, tokens.refresh_token);
    window.location.href = "/ops/panel";
  } catch (err) {
    if (alertEl) {
      alertEl.className = "alert alert-error";
      alertEl.textContent = err.message || "Error";
      alertEl.classList.remove("hidden");
    }
  }
});
