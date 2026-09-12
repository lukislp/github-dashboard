/* Minimal i18n: dictionaries, language toggle, data-i18n binding. No dependencies. */
(function () {
  "use strict";

  const DICT = {
    en: {
      refresh: "Refresh",
      refreshing: "Refreshing…",
      logout: "Sign out",
      sign_in: "Sign in with GitHub",
      login_eyebrow: "Personal overview",
      login_title: "Every repository, one screen.",
      login_tagline:
        "Open pull requests, open issues and the last five workflow runs of every repository you have access to. You sign in with your own GitHub account and see only what that account sees.",
      login_scopes_note: "Requested permissions: repo, read:org. The token is stored encrypted and revoked when you sign out.",
      fact_repos: "Own, collaborator and organisation repositories",
      fact_ci: "Failing workflows are listed first",
      fact_private: "Nothing is shared between users",
      error_denied: "You cancelled the GitHub authorisation.",
      error_state: "The sign-in request could not be verified. Please try again.",
      error_code: "GitHub did not return a valid code. Please try again.",
      error_forbidden: "This GitHub account is not allowed to use this dashboard.",
      error_github: "GitHub is not reachable at the moment. Please try again later.",
      error_expired: "Your session has expired. Please sign in again.",
      kpi_repos: "Repositories",
      kpi_prs: "Open pull requests",
      kpi_issues: "Open issues",
      kpi_failed: "Failed runs",
      kpi_running: "Running",
      kpi_repos_sub: "{private} private · {archived} archived",
      kpi_failed_sub: "in {n} repositories",
      kpi_failed_hint: "last {n} runs per repository",
      kpi_running_sub: "queued or in progress",
      kpi_prs_sub: "{drafts} drafts",
      kpi_issues_sub: "{repos} repositories with issues",
      search_placeholder: "Filter repositories…",
      owner_all: "All owners",
      only_attention: "Needs attention",
      show_archived: "Archived",
      show_forks: "Forks",
      repo_count: "{shown} of {total}",
      col_repo: "Repository",
      col_prs: "PRs",
      col_issues: "Issues",
      col_ci: "Workflows",
      col_pushed: "Last push",
      ci_failing: "Failing",
      ci_running: "Running",
      ci_passing: "Passing",
      ci_neutral: "Neutral",
      ci_none: "No runs",
      ci_skipped: "Archived",
      ci_unavailable: "Unavailable",
      badge_private: "private",
      badge_public: "public",
      badge_archived: "archived",
      badge_fork: "fork",
      details_prs: "Open pull requests",
      details_issues: "Open issues",
      details_runs: "Recent runs",
      none_open: "None",
      draft: "draft",
      more_on_github: "{n} more on GitHub",
      open_on_github: "Open on GitHub",
      failures_title: "Failed runs",
      failures_sub: "{n} failed among the last {k} runs of each repository, newest first.",
      failures_empty: "No failed runs. Everything is green.",
      footer_updated: "Updated {time}",
      footer_cached: "from cache",
      footer_live: "live",
      footer_rate: "API budget {remaining} / {limit}",
      footer_auto: "auto-refresh every {min} min",
      loading: "Loading your repositories…",
      err_rate_limited: "GitHub rate limit reached. Try again after it resets.",
      err_github: "GitHub did not answer. Try again in a moment.",
      err_network: "No connection to the dashboard server.",
      retry: "Retry",
      empty_table: "No repositories match the current filters.",
      run_success: "success",
      run_failure: "failure",
      run_timed_out: "timed out",
      run_startup_failure: "startup failure",
      run_cancelled: "cancelled",
      run_skipped: "skipped",
      run_neutral: "neutral",
      run_action_required: "action required",
      run_stale: "stale",
      run_in_progress: "in progress",
      run_queued: "queued",
      run_unknown: "unknown",
      expand: "Show details",
      collapse: "Hide details",
      just_now: "just now",
    },
    de: {
      refresh: "Aktualisieren",
      refreshing: "Aktualisiere…",
      logout: "Abmelden",
      sign_in: "Mit GitHub anmelden",
      login_eyebrow: "Persönliche Übersicht",
      login_title: "Alle Repositories, ein Bildschirm.",
      login_tagline:
        "Offene Pull Requests, offene Issues und die letzten fünf Workflow-Läufe jedes Repositories, auf das du Zugriff hast. Du meldest dich mit deinem eigenen GitHub-Konto an und siehst nur, was dieses Konto sieht.",
      login_scopes_note: "Angeforderte Berechtigungen: repo, read:org. Das Token wird verschlüsselt gespeichert und beim Abmelden widerrufen.",
      fact_repos: "Eigene, Collaborator- und Organisations-Repositories",
      fact_ci: "Fehlschlagende Workflows stehen ganz oben",
      fact_private: "Nichts wird zwischen Nutzern geteilt",
      error_denied: "Du hast die GitHub-Autorisierung abgebrochen.",
      error_state: "Die Anmeldung konnte nicht verifiziert werden. Bitte erneut versuchen.",
      error_code: "GitHub hat keinen gültigen Code geliefert. Bitte erneut versuchen.",
      error_forbidden: "Dieses GitHub-Konto darf dieses Dashboard nicht verwenden.",
      error_github: "GitHub ist gerade nicht erreichbar. Bitte später erneut versuchen.",
      error_expired: "Deine Sitzung ist abgelaufen. Bitte erneut anmelden.",
      kpi_repos: "Repositories",
      kpi_prs: "Offene Pull Requests",
      kpi_issues: "Offene Issues",
      kpi_failed: "Fehlgeschlagene Läufe",
      kpi_running: "Laufend",
      kpi_repos_sub: "{private} privat · {archived} archiviert",
      kpi_failed_sub: "in {n} Repositories",
      kpi_failed_hint: "letzte {n} Läufe je Repository",
      kpi_running_sub: "wartend oder in Arbeit",
      kpi_prs_sub: "{drafts} Entwürfe",
      kpi_issues_sub: "{repos} Repositories mit Issues",
      search_placeholder: "Repositories filtern…",
      owner_all: "Alle Owner",
      only_attention: "Handlungsbedarf",
      show_archived: "Archivierte",
      show_forks: "Forks",
      repo_count: "{shown} von {total}",
      col_repo: "Repository",
      col_prs: "PRs",
      col_issues: "Issues",
      col_ci: "Workflows",
      col_pushed: "Letzter Push",
      ci_failing: "Fehlgeschlagen",
      ci_running: "Läuft",
      ci_passing: "Erfolgreich",
      ci_neutral: "Neutral",
      ci_none: "Keine Läufe",
      ci_skipped: "Archiviert",
      ci_unavailable: "Nicht verfügbar",
      badge_private: "privat",
      badge_public: "öffentlich",
      badge_archived: "archiviert",
      badge_fork: "Fork",
      details_prs: "Offene Pull Requests",
      details_issues: "Offene Issues",
      details_runs: "Letzte Läufe",
      none_open: "Keine",
      draft: "Entwurf",
      more_on_github: "{n} weitere auf GitHub",
      open_on_github: "Auf GitHub öffnen",
      failures_title: "Fehlgeschlagene Läufe",
      failures_sub: "{n} Fehlschläge unter den letzten {k} Läufen je Repository, neueste zuerst.",
      failures_empty: "Keine fehlgeschlagenen Läufe. Alles grün.",
      footer_updated: "Aktualisiert {time}",
      footer_cached: "aus dem Cache",
      footer_live: "live",
      footer_rate: "API-Budget {remaining} / {limit}",
      footer_auto: "automatisch alle {min} Min.",
      loading: "Lade deine Repositories…",
      err_rate_limited: "GitHub-Rate-Limit erreicht. Bitte nach dem Reset erneut versuchen.",
      err_github: "GitHub antwortet nicht. Bitte gleich noch einmal versuchen.",
      err_network: "Keine Verbindung zum Dashboard-Server.",
      retry: "Erneut versuchen",
      empty_table: "Keine Repositories passen zu den aktuellen Filtern.",
      run_success: "erfolgreich",
      run_failure: "fehlgeschlagen",
      run_timed_out: "Zeitüberschreitung",
      run_startup_failure: "Startfehler",
      run_cancelled: "abgebrochen",
      run_skipped: "übersprungen",
      run_neutral: "neutral",
      run_action_required: "Aktion erforderlich",
      run_stale: "veraltet",
      run_in_progress: "in Arbeit",
      run_queued: "wartend",
      run_unknown: "unbekannt",
      expand: "Details anzeigen",
      collapse: "Details ausblenden",
      just_now: "gerade eben",
    },
  };

  const STORAGE_KEY = "ghd_lang";
  const COOKIE = "ghd_lang";

  function detect() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "de" || stored === "en") return stored;
    } catch (_) {
      /* storage may be unavailable */
    }
    const attr = document.documentElement.lang;
    if (attr === "de" || attr === "en") return attr;
    return (navigator.language || "en").toLowerCase().startsWith("de") ? "de" : "en";
  }

  let lang = detect();

  function t(key, params) {
    const entry = (DICT[lang] && DICT[lang][key]) || DICT.en[key] || key;
    if (!params) return entry;
    return entry.replace(/\{(\w+)\}/g, (m, name) =>
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : m
    );
  }

  function apply(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
    });
    scope.querySelectorAll(".lang-toggle").forEach((el) => {
      el.textContent = lang === "de" ? "EN" : "DE";
      el.setAttribute("title", lang === "de" ? "Switch to English" : "Auf Deutsch wechseln");
    });
    document.documentElement.lang = lang;
  }

  function setLang(next) {
    if (next !== "de" && next !== "en") return;
    lang = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (_) {
      /* ignore */
    }
    document.cookie = COOKIE + "=" + next + "; path=/; max-age=31536000; samesite=lax";
    apply();
    document.dispatchEvent(new CustomEvent("langchange", { detail: { lang: next } }));
  }

  function formatRelative(iso) {
    if (!iso) return "–";
    const then = new Date(iso).getTime();
    const diff = (then - Date.now()) / 1000;
    const abs = Math.abs(diff);
    if (abs < 45) return t("just_now");
    const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
    const units = [
      ["year", 31536000],
      ["month", 2592000],
      ["week", 604800],
      ["day", 86400],
      ["hour", 3600],
      ["minute", 60],
    ];
    for (const [unit, seconds] of units) {
      if (abs >= seconds || unit === "minute") {
        return rtf.format(Math.round(diff / seconds), unit);
      }
    }
    return t("just_now");
  }

  function formatDateTime(iso) {
    if (!iso) return "–";
    return new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" }).format(
      new Date(iso)
    );
  }

  function formatNumber(n) {
    return new Intl.NumberFormat(lang).format(n);
  }

  window.I18N = {
    get lang() {
      return lang;
    },
    t,
    apply,
    setLang,
    formatRelative,
    formatDateTime,
    formatNumber,
  };

  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.querySelectorAll(".lang-toggle").forEach((el) => {
      el.addEventListener("click", () => setLang(lang === "de" ? "en" : "de"));
    });
  });
})();
