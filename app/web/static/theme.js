/* Manual light/dark switch: auto -> light -> dark -> auto.
   The server already renders data-theme="light"/"dark" on <html> (or omits it for auto) from
   the ghd_theme cookie, so there is no flash of the wrong theme and no inline script is needed
   here. This file only keeps the toggle buttons, the cookie and localStorage in sync live. */
(function () {
  "use strict";

  const COOKIE = "ghd_theme";
  const STORAGE_KEY = "ghd_theme";
  const STATES = ["auto", "light", "dark"];

  const ICONS = {
    auto:
      '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true">' +
      '<circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" stroke-width="1.3"/>' +
      '<path d="M8 1.75A6.25 6.25 0 0 0 8 14.25Z"/>' +
      "</svg>",
    light:
      '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true">' +
      '<circle cx="8" cy="8" r="3.25"/>' +
      '<rect x="7.25" y="0" width="1.5" height="2.5" rx="0.75"/>' +
      '<rect x="7.25" y="13.5" width="1.5" height="2.5" rx="0.75"/>' +
      '<rect x="0" y="7.25" width="2.5" height="1.5" rx="0.75"/>' +
      '<rect x="13.5" y="7.25" width="2.5" height="1.5" rx="0.75"/>' +
      '<rect x="7.25" y="0" width="1.5" height="2.5" rx="0.75" transform="rotate(45 8 8)"/>' +
      '<rect x="7.25" y="0" width="1.5" height="2.5" rx="0.75" transform="rotate(135 8 8)"/>' +
      '<rect x="7.25" y="0" width="1.5" height="2.5" rx="0.75" transform="rotate(225 8 8)"/>' +
      '<rect x="7.25" y="0" width="1.5" height="2.5" rx="0.75" transform="rotate(315 8 8)"/>' +
      "</svg>",
    dark:
      '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true">' +
      '<path fill-rule="evenodd" d="M1.8 8A6.2 6.2 0 1 0 14.2 8A6.2 6.2 0 1 0 1.8 8Z ' +
      'M5.2 7A5.3 5.3 0 1 0 15.8 7A5.3 5.3 0 1 0 5.2 7Z"/>' +
      "</svg>",
  };

  function currentTheme() {
    const attr = document.documentElement.getAttribute("data-theme");
    return attr === "light" || attr === "dark" ? attr : "auto";
  }

  function applyAttribute(theme) {
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  }

  function persist(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (_) {
      /* storage may be unavailable */
    }
    document.cookie = COOKIE + "=" + theme + "; path=/; max-age=31536000; samesite=lax";
  }

  function label(theme) {
    return window.I18N ? window.I18N.t("theme_" + theme) : theme;
  }

  function updateButtons() {
    const theme = currentTheme();
    const text = label(theme);
    document.querySelectorAll(".theme-toggle").forEach((button) => {
      button.innerHTML = ICONS[theme];
      button.setAttribute("aria-label", text);
      button.setAttribute("title", text);
    });
  }

  function setTheme(theme) {
    applyAttribute(theme);
    persist(theme);
    updateButtons();
  }

  document.addEventListener("DOMContentLoaded", () => {
    updateButtons();
    document.querySelectorAll(".theme-toggle").forEach((button) => {
      button.addEventListener("click", () => {
        const next = STATES[(STATES.indexOf(currentTheme()) + 1) % STATES.length];
        setTheme(next);
      });
    });
  });

  document.addEventListener("langchange", updateButtons);
})();
