/* Dashboard page: fetches /api/overview and renders KPIs, repository table and failure feed. */
(function () {
  "use strict";

  const I18N = window.I18N;
  const t = I18N.t;
  const AUTO_REFRESH_MS = 5 * 60 * 1000;

  const state = {
    data: null,
    loading: false,
    filters: { q: "", owner: "", attention: false, archived: false, forks: true },
    sort: { key: "default", dir: "asc" },
    expanded: new Set(),
  };

  const $ = (sel) => document.querySelector(sel);
  const els = {
    banner: $("#banner"),
    kpis: $("#kpis"),
    rows: $("#repo-rows"),
    count: $("#repo-count"),
    failures: $("#failure-list"),
    failuresSub: $("#failures-sub"),
    footer: $("#footer"),
    status: $("#topbar-status"),
    refresh: $("#refresh"),
    q: $("#filter-q"),
    owner: $("#filter-owner"),
    attention: $("#filter-attention"),
    archived: $("#filter-archived"),
    forks: $("#filter-forks"),
  };

  // ---------- helpers ----------

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const ICONS = {
    check: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z"/></svg>',
    x: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z"/></svg>',
    dot: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="4"/></svg>',
    clock: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0Zm0 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM8 4a.75.75 0 0 1 .75.75v3.1l2 1.2a.75.75 0 1 1-.77 1.29l-2.36-1.42A.75.75 0 0 1 7.25 8.25V4.75A.75.75 0 0 1 8 4Z"/></svg>',
    dash: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 7.25h12v1.5H2z"/></svg>',
    chevron: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M4.22 6.22a.75.75 0 0 1 1.06 0L8 8.94l2.72-2.72a.75.75 0 1 1 1.06 1.06l-3.25 3.25a.75.75 0 0 1-1.06 0L4.22 7.28a.75.75 0 0 1 0-1.06Z"/></svg>',
  };

  const CI_ICON = {
    failing: ICONS.x,
    passing: ICONS.check,
    running: ICONS.clock,
    neutral: ICONS.dash,
    none: ICONS.dash,
    skipped: ICONS.dash,
    unavailable: ICONS.dash,
  };

  const CI_RANK = { failing: 0, running: 1, unavailable: 2, neutral: 3, passing: 4, none: 5, skipped: 6 };

  function needsAttention(item) {
    return (
      item.ci.state === "failing" ||
      item.repository.open_pr_count > 0 ||
      item.repository.open_issue_count > 0
    );
  }

  function runsMarkup(runs, total) {
    const cells = [];
    for (let i = 0; i < total; i += 1) {
      const run = runs[i];
      if (!run) {
        cells.push('<span class="run run--empty" aria-hidden="true"></span>');
      } else {
        const label = `${run.workflow_name} · ${t("run_" + run.status)}`;
        cells.push(`<span class="run run--${esc(run.status)}" title="${esc(label)}"></span>`);
      }
    }
    return `<span class="runs">${cells.join("")}</span>`;
  }

  // ---------- rendering ----------

  function setBanner(kind, text, retry) {
    if (!text) {
      els.banner.hidden = true;
      els.banner.innerHTML = "";
      return;
    }
    els.banner.hidden = false;
    els.banner.className = "banner" + (kind === "error" ? " banner--error" : "");
    els.banner.innerHTML =
      `<span>${esc(text)}</span>` +
      (retry ? `<button type="button" class="btn" id="banner-retry">${esc(t("retry"))}</button>` : "");
    const button = $("#banner-retry");
    if (button) button.addEventListener("click", () => load(true));
  }

  function renderKpis() {
    const d = state.data;
    const loading = !d;
    const totals = d ? d.totals : {};
    const runsPerRepo = d ? Math.max(0, ...d.repos.map((r) => r.ci.runs.length), 0) : 5;
    const drafts = d
      ? d.repos.reduce((n, r) => n + r.repository.pull_requests.filter((p) => p.is_draft).length, 0)
      : 0;
    const reposWithIssues = d ? d.repos.filter((r) => r.repository.open_issue_count > 0).length : 0;

    const tiles = [
      {
        key: "repos",
        label: t("kpi_repos"),
        value: totals.repos,
        sub: t("kpi_repos_sub", { private: totals.private ?? 0, archived: totals.archived ?? 0 }),
      },
      { key: "prs", label: t("kpi_prs"), value: totals.open_prs, sub: t("kpi_prs_sub", { drafts }), tone: "accent" },
      {
        key: "issues",
        label: t("kpi_issues"),
        value: totals.open_issues,
        sub: t("kpi_issues_sub", { repos: reposWithIssues }),
        tone: "accent",
      },
      {
        key: "failed",
        label: t("kpi_failed"),
        value: totals.failed_runs,
        sub:
          totals.failed_runs > 0
            ? t("kpi_failed_sub", { n: totals.repos_failing })
            : t("kpi_failed_hint", { n: runsPerRepo || 5 }),
        tone: totals.failed_runs > 0 ? "critical" : "good",
      },
      { key: "running", label: t("kpi_running"), value: totals.active_runs, sub: t("kpi_running_sub") },
    ];

    els.kpis.innerHTML = tiles
      .map(
        (tile) => `
        <div class="kpi ${tile.tone && !loading ? "kpi--" + tile.tone : ""} ${loading ? "is-loading" : ""}">
          <p class="kpi__label">${esc(tile.label)}</p>
          <p class="kpi__value">${loading ? "—" : esc(I18N.formatNumber(tile.value ?? 0))}</p>
          <p class="kpi__sub">${loading ? "" : esc(tile.sub)}</p>
        </div>`
      )
      .join("");
  }

  function renderOwners() {
    const d = state.data;
    if (!d) return;
    const owners = Array.from(new Set(d.repos.map((r) => r.repository.owner))).sort((a, b) =>
      a.localeCompare(b)
    );
    const current = state.filters.owner;
    els.owner.innerHTML =
      `<option value="">${esc(t("owner_all"))}</option>` +
      owners.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
    els.owner.value = owners.includes(current) ? current : "";
    state.filters.owner = els.owner.value;
  }

  function visibleRepos() {
    const d = state.data;
    if (!d) return [];
    const f = state.filters;
    const q = f.q.trim().toLowerCase();
    let items = d.repos.filter((item) => {
      const repo = item.repository;
      if (!f.archived && repo.is_archived) return false;
      if (!f.forks && repo.is_fork) return false;
      if (f.owner && repo.owner !== f.owner) return false;
      if (f.attention && !needsAttention(item)) return false;
      if (q) {
        const hay = `${repo.full_name} ${repo.description || ""} ${repo.language || ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const { key, dir } = state.sort;
    const sign = dir === "asc" ? 1 : -1;
    const comparators = {
      name: (a, b) => a.repository.full_name.localeCompare(b.repository.full_name),
      prs: (a, b) => a.repository.open_pr_count - b.repository.open_pr_count,
      issues: (a, b) => a.repository.open_issue_count - b.repository.open_issue_count,
      ci: (a, b) => CI_RANK[a.ci.state] - CI_RANK[b.ci.state],
      pushed: (a, b) => Date.parse(a.repository.pushed_at || 0) - Date.parse(b.repository.pushed_at || 0),
    };
    if (comparators[key]) {
      items = items.slice().sort((a, b) => sign * comparators[key](a, b));
    }
    return items;
  }

  function repoRow(item) {
    const repo = item.repository;
    const ci = item.ci;
    const open = state.expanded.has(repo.full_name);
    const badges = [];
    if (repo.is_private) badges.push(`<span class="badge">${esc(t("badge_private"))}</span>`);
    if (repo.is_archived) badges.push(`<span class="badge badge--archived">${esc(t("badge_archived"))}</span>`);
    if (repo.is_fork) badges.push(`<span class="badge">${esc(t("badge_fork"))}</span>`);
    const lang = repo.language
      ? `<span class="lang"><span class="lang__dot" data-color="${esc(repo.language_color || "")}"></span>${esc(repo.language)}</span>`
      : "";
    const stars = repo.stars ? `<span class="mono">★ ${esc(I18N.formatNumber(repo.stars))}</span>` : "";
    const ciLabel =
      ci.state === "failing" && ci.failed_count > 1
        ? `${t("ci_failing")} ×${ci.failed_count}`
        : t("ci_" + ci.state);
    const runsPerRepo = Math.max(ci.runs.length, state.data ? state.data.runs_per_repo || 5 : 5);

    return `
      <tr class="repo-row ci--${esc(ci.state)} ${ci.state === "failing" ? "is-failing" : ""} ${open ? "is-open" : ""}" data-repo="${esc(repo.full_name)}">
        <td class="col-repo">
          <div class="repo-name">
            <span class="repo-name__owner">${esc(repo.owner)}/</span>
            <a class="repo-name__name" href="${esc(repo.url)}" target="_blank" rel="noopener" data-stop>${esc(repo.name)}</a>
            ${badges.join("")}
          </div>
          <div class="repo-meta">${lang}${stars}</div>
        </td>
        <td class="col-num"><span class="num ${repo.open_pr_count ? "is-hot" : "is-zero"}">${repo.open_pr_count}</span></td>
        <td class="col-num"><span class="num ${repo.open_issue_count ? "is-hot" : "is-zero"}">${repo.open_issue_count}</span></td>
        <td class="col-ci">
          <div class="ci ci--${esc(ci.state)}">
            ${ci.state === "skipped" ? "" : runsMarkup(ci.runs, runsPerRepo)}
            <span class="ci__label">${CI_ICON[ci.state] || ""}${esc(ciLabel)}</span>
          </div>
        </td>
        <td class="col-pushed" title="${esc(I18N.formatDateTime(repo.pushed_at))}">${esc(I18N.formatRelative(repo.pushed_at))}</td>
        <td class="col-toggle">
          <button type="button" class="toggle" aria-expanded="${open}" aria-label="${esc(t(open ? "collapse" : "expand"))}">${ICONS.chevron}</button>
        </td>
      </tr>
      ${open ? detailRow(item) : ""}`;
  }

  function detailRow(item) {
    const repo = item.repository;
    const ci = item.ci;

    const prList = repo.pull_requests.length
      ? `<ul>${repo.pull_requests
          .map(
            (p) => `<li>
              <span class="id">#${p.number}</span>
              <a class="title" href="${esc(p.url)}" target="_blank" rel="noopener" title="${esc(p.title)}">${esc(p.title)}</a>
              ${p.is_draft ? `<span class="tag">${esc(t("draft"))}</span>` : ""}
              <span class="by">${esc(p.author || "")}</span>
            </li>`
          )
          .join("")}</ul>` +
        (repo.open_pr_count > repo.pull_requests.length
          ? `<a class="more" href="${esc(repo.url)}/pulls" target="_blank" rel="noopener">${esc(t("more_on_github", { n: repo.open_pr_count - repo.pull_requests.length }))}</a>`
          : "")
      : `<p class="empty">${esc(t("none_open"))}</p>`;

    const issueList = repo.issues.length
      ? `<ul>${repo.issues
          .map(
            (i) => `<li>
              <span class="id">#${i.number}</span>
              <a class="title" href="${esc(i.url)}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title)}</a>
              <span class="by">${esc(i.author || "")}</span>
            </li>`
          )
          .join("")}</ul>` +
        (repo.open_issue_count > repo.issues.length
          ? `<a class="more" href="${esc(repo.url)}/issues" target="_blank" rel="noopener">${esc(t("more_on_github", { n: repo.open_issue_count - repo.issues.length }))}</a>`
          : "")
      : `<p class="empty">${esc(t("none_open"))}</p>`;

    const runList = ci.runs.length
      ? `<ul>${ci.runs
          .map(
            (r) => `<li class="run-line">
              <span class="run run--${esc(r.status)}"></span>
              <a class="title" href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.title)}">${esc(r.workflow_name)}</a>
              <span class="branch">${esc(r.branch || "")}</span>
              <span class="status">${esc(t("run_" + r.status))} · ${esc(I18N.formatRelative(r.updated_at))}</span>
            </li>`
          )
          .join("")}</ul>` +
        `<a class="more" href="${esc(repo.url)}/actions" target="_blank" rel="noopener">${esc(t("open_on_github"))}</a>`
      : `<p class="empty">${esc(t("ci_" + ci.state))}</p>`;

    return `
      <tr class="detail-row" data-detail="${esc(repo.full_name)}">
        <td colspan="6">
          <div class="details">
            <div><h4>${esc(t("details_prs"))} · ${repo.open_pr_count}</h4>${prList}</div>
            <div><h4>${esc(t("details_issues"))} · ${repo.open_issue_count}</h4>${issueList}</div>
            <div><h4>${esc(t("details_runs"))}</h4>${runList}</div>
          </div>
        </td>
      </tr>`;
  }

  function renderTable() {
    const d = state.data;
    if (!d) {
      els.rows.innerHTML = `<tr><td colspan="6" class="table-empty">${esc(t("loading"))}</td></tr>`;
      els.count.textContent = "";
      return;
    }
    const items = visibleRepos();
    els.count.textContent = t("repo_count", { shown: items.length, total: d.repos.length });
    els.rows.innerHTML = items.length
      ? items.map(repoRow).join("")
      : `<tr><td colspan="6" class="table-empty">${esc(t("empty_table"))}</td></tr>`;

    // Language colours come from GitHub; set them via CSSOM because the CSP forbids inline styles.
    els.rows.querySelectorAll(".lang__dot[data-color]").forEach((dot) => {
      if (/^#[0-9a-f]{3,8}$/i.test(dot.dataset.color)) dot.style.backgroundColor = dot.dataset.color;
    });

    document.querySelectorAll(".sort").forEach((button) => {
      button.classList.remove("is-asc", "is-desc");
      if (button.dataset.sort === state.sort.key) button.classList.add("is-" + state.sort.dir);
    });
  }

  function renderFailures() {
    const d = state.data;
    if (!d) {
      els.failures.innerHTML = "";
      els.failuresSub.textContent = "";
      return;
    }
    const runsPerRepo = d.runs_per_repo || 5;
    els.failuresSub.textContent = t("failures_sub", { n: d.failures.length, k: runsPerRepo });
    if (!d.failures.length) {
      els.failures.innerHTML = `<li class="empty">${ICONS.check}${esc(t("failures_empty"))}</li>`;
      return;
    }
    els.failures.innerHTML = d.failures
      .map(
        (f) => `<li class="failure">
          <span class="run run--${esc(f.run.status)}" aria-hidden="true"></span>
          <span class="failure__repo">${esc(f.repo_full_name)}</span>
          <span class="failure__title"><a class="workflow" href="${esc(f.run.url)}" target="_blank" rel="noopener">${esc(f.run.workflow_name)}</a><span class="sep">·</span>${esc(f.run.title)}</span>
          <span class="failure__branch">${esc(f.run.branch || "")}</span>
          <span class="failure__time" title="${esc(I18N.formatDateTime(f.run.updated_at))}">${esc(I18N.formatRelative(f.run.updated_at))}</span>
        </li>`
      )
      .join("");
  }

  function renderFooter() {
    const d = state.data;
    if (!d) {
      els.footer.innerHTML = "";
      els.status.textContent = "";
      return;
    }
    const parts = [
      t("footer_updated", { time: I18N.formatRelative(d.generated_at) }) +
        " · " +
        (d.from_cache ? t("footer_cached") : t("footer_live")),
    ];
    if (d.rate_limit) {
      parts.push(t("footer_rate", { remaining: I18N.formatNumber(d.rate_limit.remaining), limit: I18N.formatNumber(d.rate_limit.limit) }));
    }
    parts.push(t("footer_auto", { min: AUTO_REFRESH_MS / 60000 }));
    els.footer.innerHTML = parts.map((p) => `<span>${esc(p)}</span>`).join("");
    els.status.textContent = t("footer_updated", { time: I18N.formatDateTime(d.generated_at) });
  }

  function renderAll() {
    renderKpis();
    renderOwners();
    renderTable();
    renderFailures();
    renderFooter();
  }

  // ---------- data ----------

  async function load(force) {
    if (state.loading) return;
    state.loading = true;
    els.refresh.disabled = true;
    els.refresh.textContent = t("refreshing");
    if (!state.data) setBanner("info", t("loading"));

    try {
      const response = await fetch("/api/overview" + (force ? "?refresh=1" : ""), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (response.status === 401) {
        window.location.assign("/login?error=expired");
        return;
      }
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const key = body.error === "rate_limited" ? "err_rate_limited" : "err_github";
        setBanner("error", t(key), true);
        return;
      }
      const data = await response.json();
      data.runs_per_repo = Math.max(...data.repos.map((r) => r.ci.runs.length), 0) || 5;
      state.data = data;
      setBanner(null);
      renderAll();
    } catch (_) {
      setBanner("error", t("err_network"), true);
    } finally {
      state.loading = false;
      els.refresh.disabled = false;
      els.refresh.textContent = t("refresh");
    }
  }

  // ---------- events ----------

  function bind() {
    els.refresh.addEventListener("click", () => load(true));

    els.q.addEventListener("input", () => {
      state.filters.q = els.q.value;
      renderTable();
    });
    els.owner.addEventListener("change", () => {
      state.filters.owner = els.owner.value;
      renderTable();
    });
    for (const [key, el] of [["attention", els.attention], ["archived", els.archived], ["forks", els.forks]]) {
      el.addEventListener("change", () => {
        state.filters[key] = el.checked;
        renderTable();
      });
    }

    document.querySelectorAll(".sort").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.sort;
        if (state.sort.key === key) {
          state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
        } else {
          state.sort = { key, dir: key === "name" ? "asc" : "desc" };
        }
        renderTable();
      });
    });

    els.rows.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      const row = event.target.closest(".repo-row");
      if (!row) return;
      const name = row.dataset.repo;
      if (state.expanded.has(name)) state.expanded.delete(name);
      else state.expanded.add(name);
      renderTable();
    });

    document.addEventListener("langchange", renderAll);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && state.data) {
        const age = Date.now() - Date.parse(state.data.generated_at);
        if (age > AUTO_REFRESH_MS) load(false);
      }
    });
    setInterval(() => {
      if (!document.hidden) load(false);
    }, AUTO_REFRESH_MS);
  }

  document.addEventListener("DOMContentLoaded", () => {
    bind();
    renderAll();
    load(false);
  });
})();
