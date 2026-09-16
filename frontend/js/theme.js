/** Tema claro/oscuro. Persistido en localStorage; si no hay elección, sigue al sistema. */
(function () {
  const KEY = "fp_theme";
  const root = document.documentElement;

  function systemDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function stored() {
    try {
      const v = localStorage.getItem(KEY);
      if (v === "dark" || v === "light") return v;
    } catch (_) {
      /* private mode / blocked storage */
    }
    return null;
  }

  function resolved(pref) {
    if (pref === "dark" || pref === "light") return pref;
    return systemDark() ? "dark" : "light";
  }

  function paintToggles(theme) {
    const goingLight = theme === "dark";
    const label = goingLight ? "Cambiar a modo claro" : "Cambiar a modo oscuro";
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.setAttribute("aria-label", label);
      btn.setAttribute("title", label);
      btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
      const text = btn.querySelector(".theme-toggle-text");
      if (text) text.textContent = goingLight ? "Modo claro" : "Modo oscuro";
    });
  }

  function apply(pref, persist) {
    const theme = resolved(pref);
    root.setAttribute("data-theme", theme);
    root.style.colorScheme = theme;
    if (persist) {
      try {
        localStorage.setItem(KEY, theme);
      } catch (_) {
        /* ignore */
      }
    }
    paintToggles(theme);
    document.dispatchEvent(new CustomEvent("fp-theme-change", { detail: { theme } }));
  }

  (function boot() {
    try {
      const q = new URLSearchParams(window.location.search).get("theme");
      if (q === "dark" || q === "light") {
        apply(q, true);
        try {
          const url = new URL(window.location.href);
          url.searchParams.delete("theme");
          const cleaned = url.pathname + (url.searchParams.toString() ? `?${url.searchParams}` : "") + url.hash;
          window.history.replaceState({}, "", cleaned);
        } catch (_) {
          /* ignore */
        }
        return;
      }
    } catch (_) {
      /* ignore */
    }
    apply(stored(), false);
  })();

  window.FPTheme = {
    current: function () {
      return root.getAttribute("data-theme") || "light";
    },
    set: function (theme) {
      apply(theme, true);
    },
    toggle: function () {
      apply(root.getAttribute("data-theme") === "dark" ? "light" : "dark", true);
    },
  };

  function bind() {
    paintToggles(window.FPTheme.current());
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", function () {
        window.FPTheme.toggle();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      if (!stored()) apply(null, false);
    });
  }
})();
