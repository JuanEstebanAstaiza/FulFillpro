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
  get deviceId() {
    let id = localStorage.getItem("fp_device_id");
    if (!id) {
      id = "PC-" + Math.random().toString(36).slice(2, 8).toUpperCase() + "-" + Date.now().toString(36).toUpperCase();
      localStorage.setItem("fp_device_id", id);
    }
    return id;
  },
  setDeviceId(id) {
    localStorage.setItem("fp_device_id", (id || "").trim().toUpperCase());
  },
  get deviceSoft() {
    let s = localStorage.getItem("fp_device_soft");
    if (!s) {
      const raw = [navigator.platform, navigator.language, screen.width, screen.height, navigator.hardwareConcurrency || 0].join("|");
      s = btoa(unescape(encodeURIComponent(raw))).slice(0, 48);
      localStorage.setItem("fp_device_soft", s);
    }
    return s;
  },
  get licenseCode() {
    return localStorage.getItem("fp_license") || "";
  },
  setLicenseCode(code) {
    localStorage.setItem("fp_license", (code || "").trim().toUpperCase());
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
  me() {
    return this.request("/api/auth/me");
  },
  logout() {
    return this.request("/api/auth/logout", {
      method: "POST",
      body: JSON.stringify({ refresh_token: localStorage.getItem("fp_refresh") || "" }),
    }).finally(() => this.clearTokens());
  },
  activateLicense(payload) {
    return this.request("/api/licenses/activate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  licenseStatus() {
    return this.request("/api/licenses/status");
  },
  listOrders(page = 1) {
    return this.request(`/api/orders?page=${page}&page_size=20`);
  },
  async processFile(file, countQuota = true) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("license_code", this.licenseCode);
    fd.append("device_id", this.deviceId);
    fd.append("device_soft", this.deviceSoft);
    fd.append("count_quota", countQuota ? "true" : "false");
    const res = await this.request("/api/process", { method: "POST", body: fd });
    return res; // FileResponse
  },
};
