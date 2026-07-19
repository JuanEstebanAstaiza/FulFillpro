const API = {
  get token() {
    return localStorage.getItem("fp_access") || "";
  },
  setTokens(access, refresh) {
    localStorage.setItem("fp_access", access || "");
    localStorage.setItem("fp_refresh", refresh || "");
  },
  clearTokens() {
    localStorage.removeItem("fp_access");
    localStorage.removeItem("fp_refresh");
  },

  async request(path, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (!(options.body instanceof FormData)) {
      headers["Content-Type"] = headers["Content-Type"] || "application/json";
    }
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;

    let res = await fetch(path, { ...options, headers });

    if (res.status === 401 && localStorage.getItem("fp_refresh")) {
      const refreshed = await this.tryRefresh();
      if (refreshed) {
        headers["Authorization"] = `Bearer ${this.token}`;
        res = await fetch(path, { ...options, headers });
      }
    }

    const ct = res.headers.get("content-type") || "";
    if (!res.ok) {
      let detail = res.statusText;
      if (ct.includes("application/json")) {
        const j = await res.json().catch(() => ({}));
        detail = j.detail || JSON.stringify(j);
        if (Array.isArray(detail)) detail = detail.map((d) => d.msg || d).join("; ");
      } else {
        detail = await res.text();
      }
      throw new Error(detail || "Error de red");
    }

    if (res.status === 204) return null;
    if (ct.includes("application/json")) return res.json();
    return res;
  },

  async tryRefresh() {
    const refresh = localStorage.getItem("fp_refresh");
    if (!refresh) return false;
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) {
        this.clearTokens();
        return false;
      }
      const data = await res.json();
      this.setTokens(data.access_token, data.refresh_token);
      return true;
    } catch {
      this.clearTokens();
      return false;
    }
  },

  login(email, password) {
    return this.request("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },
  loginPlatform(email, password) {
    return this.request("/api/auth/login/platform", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  },
  register(payload) {
    return this.request("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  me() {
    return this.request("/api/auth/me");
  },
  logout() {
    return this.request("/api/auth/logout", {
      method: "POST",
      body: JSON.stringify({ refresh_token: localStorage.getItem("fp_refresh") || "" }),
    }).finally(() => this.clearTokens());
  },
  dashboard() {
    return this.request("/api/licenses/dashboard");
  },
  listOrders(page = 1) {
    return this.request(`/api/orders?page=${page}&page_size=30`);
  },
  async processFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    // Respuesta 202 + job; el frontend hace polling
    return this.request("/api/process", { method: "POST", body: fd });
  },
  jobStatus(orderId) {
    return this.request(`/api/jobs/${orderId}`);
  },
  async jobDownload(orderId) {
    return this.request(`/api/jobs/${orderId}/download`);
  },
  queueStats() {
    return this.request("/api/queue/stats");
  },
  legalPending() {
    return this.request("/api/legal/pending");
  },
  legalSign(payload) {
    return this.request("/api/legal/sign", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  companyEmployees() {
    return this.request("/api/company/employees");
  },
  createEmployee(payload) {
    return this.request("/api/company/employees", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  toggleEmployee(id) {
    return this.request(`/api/company/employees/${id}/toggle`, { method: "POST" });
  },
  analyticsCurrent() {
    return this.request("/api/analytics/current");
  },
  analyticsWeek(id) {
    return this.request(`/api/analytics/weeks/${id}`);
  },
  analyticsConsolidate(id, force = false) {
    const q = force ? "?force=true" : "";
    return this.request(`/api/analytics/weeks/${id}/consolidate${q}`, { method: "POST" });
  },
  async analyticsDownload(id, format = "txt") {
    const res = await this.request(
      `/api/analytics/weeks/${id}/download?format=${encodeURIComponent(format)}`
    );
    return res; // Response with blob body
  },
};
