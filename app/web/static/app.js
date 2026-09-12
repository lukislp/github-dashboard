/* Dashboard page: fetches /api/overview and renders KPIs, repository table and failure feed. */
(function () {
  "use strict";

  const I18N = window.I18N;
  const t = I18N.t;
  const AUTO_REFRESH_MS = 5 * 60 * 1000;

  const state = {
    data: null,
    loading: false,
    filters: { q: "", owner: "", attention: false, archived: false, forks: true, bots: true },
    sort: { key: "default", dir: "asc" },
    expanded: new Set(),
    inboxFilter: null,
  };

  const $ = (sel) => document.querySelector(sel);
  const els = {
    banner: $("#banner"),
    kpis: $("#kpis"),
    inbox: $("#inbox"),
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
    bots: $("#filter-bots"),
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
    eye: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.25c-3.6 0-6.5 3.1-7.4 4.4a.6.6 0 0 0 0 .7c.9 1.3 3.8 4.4 7.4 4.4s6.5-3.1 7.4-4.4a.6.6 0 0 0 0-.7c-.9-1.3-3.8-4.4-7.4-4.4Zm0 7.75a3.25 3.25 0 1 1 0-6.5 3.25 3.25 0 0 1 0 6.5Zm0-1.5a1.75 1.75 0 1 0 0-3.5 1.75 1.75 0 0 0 0 3.5Z"/></svg>',
    pencil: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M11.01 1.43a1.75 1.75 0 0 1 2.47 0l1.09 1.09a1.75 1.75 0 0 1 0 2.47l-8.61 8.61a1.7 1.7 0 0 1-.76.44l-3.25.93a.75.75 0 0 1-.93-.93l.93-3.25c.08-.29.24-.55.44-.76ZM12.19 6.25 9.75 3.81l-6.29 6.29a.2.2 0 0 0-.06.1l-.56 1.96 1.96-.56a.2.2 0 0 0 .11-.06Zm1.24-3.76a.25.25 0 0 0-.35 0L11.81 3.75l1.44 1.44 1.26-1.26a.25.25 0 0 0 0-.35Z"/></svg>',
    person: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M10.56 8.07a6 6 0 0 1 3.43 5.14.75.75 0 1 1-1.5.07 4.5 4.5 0 0 0-8.98 0 .75.75 0 0 1-1.5-.07 6 6 0 0 1 3.43-5.14 4 4 0 1 1 5.12 0ZM10.5 5a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z"/></svg>',
    comment: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.75 1A1.75 1.75 0 0 0 1 2.75v7.5c0 .966.784 1.75 1.75 1.75H6v2.19c0 .34.41.51.65.27L9.31 12h3.94A1.75 1.75 0 0 0 15 10.25v-7.5A1.75 1.75 0 0 0 13.25 1Z"/></svg>',
    alert: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M6.46 1.05c.66-1.24 2.43-1.24 3.09 0l6.08 11.38A1.75 1.75 0 0 1 14.08 15H1.92a1.75 1.75 0 0 1-1.55-2.57Zm1.29 4.7v2.5a.75.75 0 0 0 1.5 0v-2.5a.75.75 0 0 0-1.5 0ZM9 11a1 1 0 1 0-2 0 1 1 0 0 0 2 0Z"/></svg>',
    shield: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M7.467.133a1.75 1.75 0 0 1 1.066 0l5.25 1.68A1.75 1.75 0 0 1 15 3.48V7c0 1.566-.32 3.182-1.303 4.682-.983 1.498-2.585 2.813-5.032 3.855a1.7 1.7 0 0 1-1.33 0c-2.447-1.042-4.049-2.357-5.032-3.855C1.32 10.182 1 8.566 1 7V3.48a1.75 1.75 0 0 1 1.217-1.667Zm.61 1.429a.25.25 0 0 0-.153 0l-5.25 1.68a.25.25 0 0 0-.174.238V7c0 1.36.275 2.666 1.057 3.86.784 1.194 2.121 2.34 4.366 3.297a.2.2 0 0 0 .154 0c2.245-.956 3.582-2.104 4.366-3.298C13.225 9.666 13.5 8.36 13.5 7V3.48a.25.25 0 0 0-.174-.237l-5.25-1.68ZM11.28 6.28l-3.5 3.5a.75.75 0 0 1-1.06 0l-1.5-1.5a.75.75 0 0 1 1.06-1.06l.97.97 2.97-2.97a.75.75 0 0 1 1.06 1.06Z"/></svg>',
    bell: '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 16a2 2 0 0 0 1.985-1.75c.017-.137-.097-.25-.235-.25h-3.5c-.138 0-.252.113-.235.25A2 2 0 0 0 8 16ZM8 1.5A3.5 3.5 0 0 0 4.5 5v2.947c0 .346-.102.683-.294.97l-1.703 2.556a.018.018 0 0 0-.003.01l.001.006c0 .002.002.004.004.006l.006.004.007.001h11.964l.007-.001.006-.004.004-.006.001-.007a.017.017 0 0 0-.003-.01l-1.703-2.554a1.75 1.75 0 0 1-.294-.97V5A3.5 3.5 0 0 0 8 1.5ZM3 5a5 5 0 0 1 10 0v2.947c0 .05.015.098.042.139l1.703 2.555A1.518 1.518 0 0 1 13.482 13H2.518a1.518 1.518 0 0 1-1.263-2.36l1.703-2.554A.25.25 0 0 0 3 7.947Z"/></svg>',
  };

  const STATE_ICON = {
    conflict: ICONS.alert,
    failing: ICONS.x,
    changes_requested: ICONS.pencil,
    draft: ICONS.dash,
    pending_checks: ICONS.clock,
    ready: ICONS.check,
    awaiting_review: ICONS.eye,
  };

  const STATE_BUCKET = {
    conflict: "critical",
    failing: "critical",
    changes_requested: "warning",
    draft: "neutral",
    pending_checks: "neutral",
    ready: "good",
    awaiting_review: "neutral",
  };

  const BUCKET_ORDER = ["critical", "warning", "good", "neutral"];

  const INBOX_KINDS = ["review_requested", "changes_requested", "assigned", "mentioned"];
  const INBOX_ICON = {
    review_requested: ICONS.eye,
    changes_requested: ICONS.pencil,
    assigned: ICONS.person,
    mentioned: ICONS.comment,
  };

  const NOTIF_ICON = {
    review_requested: ICONS.eye,
    mention: ICONS.comment,
    team_mention: ICONS.comment,
    assign: ICONS.person,
    author: ICONS.pencil,
    comment: ICONS.comment,
    subscribed: ICONS.dot,
    state_change: ICONS.dot,
    ci_activity: ICONS.clock,
    security_alert: ICONS.alert,
    manual: ICONS.dot,
    invitation: ICONS.person,
  };

  function notifReasonLabel(reason) {
    const key = "notif_reason_" + reason;
    const label = t(key);
    return label === key ? reason : label;
  }

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
      item.repository.open_issue_count > 0 ||
      (item.security && item.security.has_critical)
    );
  }

  function severityBreakdown(counts) {
    const parts = [];
    if (counts.critical) parts.push(`${counts.critical} ${t("sev_critical")}`);
    if (counts.high) parts.push(`${counts.high} ${t("sev_high")}`);
    if (counts.moderate) parts.push(`${counts.moderate} ${t("sev_moderate")}`);
    if (counts.low) parts.push(`${counts.low} ${t("sev_low")}`);
    return parts.length ? parts.join(", ") : t("security_none");
  }

  function securityLines(security) {
    const dep = security.dependabot ? severityBreakdown(security.dependabot) : t("security_unavailable");
    const cs = security.code_scanning ? severityBreakdown(security.code_scanning) : t("security_unavailable");
    const secrets =
      security.secret_scanning == null
        ? t("security_unavailable")
        : security.secret_scanning > 0
          ? t("security_secrets_count", { n: security.secret_scanning })
          : t("security_none");
    return [
      `${t("security_dependabot")}: ${dep}`,
      `${t("security_code_scanning")}: ${cs}`,
      `${t("security_secrets")}: ${secrets}`,
    ];
  }

  function alertsTone(security) {
    if (security.has_critical) return "critical";
    const high =
      (security.dependabot ? security.dependabot.high : 0) +
      (security.code_scanning ? security.code_scanning.high : 0);
    if (high > 0) return "warning";
    return "muted";
  }

  function alertsCellMarkup(security) {
    const tone = alertsTone(security);
    const title = securityLines(security).join("\n");
    return `<span class="alert-cell alert-cell--${tone}" title="${esc(title)}">${ICONS.shield}<span class="mono">${esc(I18N.formatNumber(security.total))}</span></span>`;
  }

  function visiblePrs(repo) {
    return state.filters.bots ? repo.pull_requests : repo.pull_requests.filter((p) => !p.is_bot);
  }

  function displayedPrCount(repo) {
    if (state.filters.bots) return repo.open_pr_count;
    const bots = repo.pull_requests.filter((p) => p.is_bot).length;
    return Math.max(0, repo.open_pr_count - bots);
  }

  function prDotsMarkup(prs) {
    if (!prs.length) return "";
    const counts = { critical: 0, warning: 0, good: 0, neutral: 0 };
    const byState = {};
    prs.forEach((p) => {
      const bucket = STATE_BUCKET[p.state] || "neutral";
      counts[bucket] += 1;
      byState[p.state] = (byState[p.state] || 0) + 1;
    });
    const dots = BUCKET_ORDER.filter((b) => counts[b] > 0)
      .slice(0, 3)
      .map((b) => `<span class="pr-dot pr-dot--${b}"></span>`)
      .join("");
    if (!dots) return "";
    const title = Object.keys(byState)
      .map((s) => `${byState[s]} ${t("state_" + s)}`)
      .join(" · ");
    return `<span class="pr-dots" title="${esc(title)}">${dots}</span>`;
  }

  function stateChipMarkup(pr) {
    const bucket = STATE_BUCKET[pr.state] || "neutral";
    const icon = STATE_ICON[pr.state] || "";
    return `<span class="state-chip state-chip--${bucket}">${icon}${esc(t("state_" + pr.state))}</span>`;
  }

  function staleTagMarkup() {
    return `<span class="tag tag--stale">${ICONS.clock}${esc(t("stale_tag"))}</span>`;
  }

  function botTagMarkup() {
    return `<span class="tag">${esc(t("bot_tag"))}</span>`;
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
    const reposWithIssues = d ? d.repos.filter((r) => r.repository.open_issue_count > 0).length : 0;

    let prsSub = t("kpi_prs_sub", { human: totals.human_prs ?? 0, bots: totals.bot_prs ?? 0 });
    if (totals.stale_prs > 0) prsSub += " · " + t("stale_suffix", { n: totals.stale_prs });
    let issuesSub = t("kpi_issues_sub", { repos: reposWithIssues });
    if (totals.stale_issues > 0) issuesSub += " · " + t("stale_suffix", { n: totals.stale_issues });

    const securitySub = t("kpi_security_sub", {
      critical: totals.security_critical ?? 0,
      high: totals.security_high ?? 0,
      secrets: totals.secret_alerts ?? 0,
    });
    const securityTone =
      (totals.security_critical ?? 0) > 0 || (totals.secret_alerts ?? 0) > 0
        ? "critical"
        : (totals.security_high ?? 0) > 0
          ? "warning"
          : "good";

    const unreleasedTone = (totals.repos_unreleased ?? 0) > 0 ? "warning" : "good";

    const notifAvailable = d ? d.notifications_available : false;
    let notifValue = notifAvailable ? totals.notifications_unread ?? 0 : null;
    let notifSub;
    if (!notifAvailable) {
      notifSub = t("kpi_notifications_unavailable");
    } else if (!totals.notifications_unread) {
      notifSub = t("kpi_notifications_empty");
    } else {
      const reasons = Object.entries(totals.notifications_by_reason || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 2)
        .map(([reason, n]) => `${n} ${notifReasonLabel(reason)}`);
      notifSub = reasons.join(" · ");
    }

    const tiles = [
      {
        key: "repos",
        label: t("kpi_repos"),
        value: totals.repos,
        sub: t("kpi_repos_sub", { private: totals.private ?? 0, archived: totals.archived ?? 0 }),
      },
      {
        key: "prs",
        label: t("kpi_prs"),
        value: state.filters.bots ? totals.open_prs : totals.human_prs,
        sub: prsSub,
        tone: "accent",
      },
      {
        key: "issues",
        label: t("kpi_issues"),
        value: totals.open_issues,
        sub: issuesSub,
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
      {
        key: "security",
        label: t("kpi_security"),
        value: totals.security_total,
        sub: securitySub,
        tone: securityTone,
      },
      {
        key: "unreleased",
        label: t("kpi_unreleased"),
        value: totals.repos_unreleased,
        sub: t("kpi_unreleased_sub", { n: totals.unreleased_commits ?? 0 }),
        tone: unreleasedTone,
      },
      {
        key: "notifications",
        label: t("kpi_notifications"),
        value: notifValue,
        sub: notifSub,
        tone: notifAvailable && totals.notifications_unread > 0 ? "accent" : null,
      },
    ];

    els.kpis.innerHTML = tiles
      .map(
        (tile) => `
        <div class="kpi ${tile.tone && !loading ? "kpi--" + tile.tone : ""} ${loading ? "is-loading" : ""}">
          <p class="kpi__label">${esc(tile.label)}</p>
          <p class="kpi__value">${loading || tile.value == null ? "—" : esc(I18N.formatNumber(tile.value))}</p>
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

  function inboxItems(inbox) {
    const items = [];
    INBOX_KINDS.forEach((kind) => {
      (inbox[kind] || []).forEach((item) => items.push(item));
    });
    return items;
  }

  function inboxItemMarkup(item) {
    const kindLabel = t("inbox_kind_" + item.kind);
    return `<li class="inbox-item">
      <span class="inbox-item__kind">${INBOX_ICON[item.kind] || ""}${esc(kindLabel)}</span>
      <span class="inbox-item__repo mono">${esc(item.repo_full_name)}</span>
      <a class="inbox-item__title" href="${esc(item.url)}" target="_blank" rel="noopener" title="${esc(item.title)}">#${item.number} ${esc(item.title)}</a>
      ${item.is_draft ? `<span class="tag">${esc(t("draft"))}</span>` : ""}
      <span class="inbox-item__author">${esc(item.author || "")}</span>
      <span class="inbox-item__time" title="${esc(I18N.formatDateTime(item.updated_at))}">${esc(I18N.formatRelative(item.updated_at))}</span>
    </li>`;
  }

  function notificationItemMarkup(n) {
    const label = notifReasonLabel(n.reason);
    const icon = NOTIF_ICON[n.reason] || ICONS.dot;
    const titleLink = n.subject_url
      ? `<a class="inbox-item__title" href="${esc(n.subject_url)}" target="_blank" rel="noopener" title="${esc(n.subject_title)}">${esc(n.subject_title)}</a>`
      : `<span class="inbox-item__title" title="${esc(n.subject_title)}">${esc(n.subject_title)}</span>`;
    return `<li class="inbox-item">
      <span class="inbox-item__kind">${icon}${esc(label)}</span>
      <span class="inbox-item__repo mono">${esc(n.repo_full_name)}</span>
      ${titleLink}
      <span class="inbox-item__time" title="${esc(I18N.formatDateTime(n.updated_at))}">${esc(I18N.formatRelative(n.updated_at))}</span>
    </li>`;
  }

  function renderInbox() {
    const d = state.data;
    if (!d) {
      els.inbox.innerHTML = "";
      return;
    }
    const inbox = d.inbox;
    const notifAvailable = d.notifications_available;
    const notifUnread = d.totals.notifications_unread ?? 0;
    const nothingWaiting = (!inbox || inbox.total === 0) && (!notifAvailable || notifUnread === 0);
    if (nothingWaiting) {
      els.inbox.innerHTML = `<p class="inbox-empty">${ICONS.check}${esc(t("inbox_empty"))}</p>`;
      return;
    }

    const tiles =
      INBOX_KINDS.map((kind) => {
        const count = (inbox[kind] || []).length;
        const active = state.inboxFilter === kind;
        return `<button type="button" class="inbox-tile ${active ? "is-active" : ""}" data-kind="${kind}" aria-pressed="${active}">
        <span class="inbox-tile__label">${INBOX_ICON[kind] || ""}${esc(t("inbox_" + kind))}</span>
        <span class="inbox-tile__value">${esc(I18N.formatNumber(count))}</span>
      </button>`;
      }).join("") +
      (() => {
        const active = state.inboxFilter === "notifications";
        const value = notifAvailable ? esc(I18N.formatNumber(notifUnread)) : "—";
        const titleAttr = notifAvailable ? "" : ` title="${esc(t("notifications_scope_missing"))}"`;
        return `<button type="button" class="inbox-tile ${active ? "is-active" : ""}" data-kind="notifications" aria-pressed="${active}"${titleAttr}>
        <span class="inbox-tile__label">${ICONS.bell}${esc(t("inbox_notifications"))}</span>
        <span class="inbox-tile__value">${value}</span>
      </button>`;
      })();

    let list;
    if (state.inboxFilter === "notifications") {
      const items = notifAvailable ? d.notifications.filter((n) => n.unread) : [];
      list = items.length
        ? items.map(notificationItemMarkup).join("")
        : `<li class="inbox-empty-row">${esc(notifAvailable ? t("inbox_empty_list") : t("notifications_scope_missing"))}</li>`;
    } else {
      const items = state.inboxFilter ? inbox[state.inboxFilter] || [] : inboxItems(inbox);
      list = items.length
        ? items.map(inboxItemMarkup).join("")
        : `<li class="inbox-empty-row">${esc(t("inbox_empty_list"))}</li>`;
    }

    els.inbox.innerHTML = `
      <div class="section-head">
        <h2>${esc(t("inbox_title"))}</h2>
        <p class="section-sub">${esc(t("inbox_filter_hint"))}</p>
      </div>
      <div class="inbox__tiles">${tiles}</div>
      <ul class="inbox__list">${list}</ul>`;

    els.inbox.querySelectorAll(".inbox-tile").forEach((button) => {
      button.addEventListener("click", () => {
        const kind = button.dataset.kind;
        state.inboxFilter = state.inboxFilter === kind ? null : kind;
        renderInbox();
      });
    });
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
      alerts: (a, b) => (a.security.total || 0) - (b.security.total || 0),
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
    const unreleasedCommits = item.release ? item.release.unreleased_commits : null;
    const unreleasedBadge =
      unreleasedCommits > 0
        ? `<span class="badge" title="${esc(t("badge_unreleased_title", { n: unreleasedCommits }))}">${esc(t("badge_unreleased", { n: unreleasedCommits }))}</span>`
        : "";
    const alertsCell = repo.is_archived
      ? `<span class="num is-zero">–</span>`
      : alertsCellMarkup(item.security);
    const longRunning = ci.long_running_count > 0;
    const ciLabel = longRunning
      ? t("ci_running_slow")
      : ci.state === "failing" && ci.failed_count > 1
        ? `${t("ci_failing")} ×${ci.failed_count}`
        : t("ci_" + ci.state);
    const runsPerRepo = Math.max(ci.runs.length, state.data ? state.data.runs_per_repo || 5 : 5);

    const prs = visiblePrs(repo);
    const prCount = displayedPrCount(repo);
    const dots = prDotsMarkup(prs);

    const commit = repo.last_commit;
    const commitLine = commit
      ? `<a class="pushed__commit" href="${esc(commit.url)}" target="_blank" rel="noopener" title="${esc(commit.author_login || commit.author_name || "")} · ${esc(commit.headline)}">${esc(commit.author_login || commit.author_name || "")} · ${esc(commit.headline)}</a>`
      : "";

    return `
      <tr class="repo-row ci--${esc(ci.state)} ${ci.state === "failing" ? "is-failing" : ""} ${open ? "is-open" : ""}" data-repo="${esc(repo.full_name)}">
        <td class="col-repo">
          <div class="repo-name">
            <span class="repo-name__owner">${esc(repo.owner)}/</span>
            <a class="repo-name__name" href="${esc(repo.url)}" target="_blank" rel="noopener" data-stop>${esc(repo.name)}</a>
            ${badges.join("")}
          </div>
          <div class="repo-meta">${lang}${stars}${unreleasedBadge}</div>
        </td>
        <td class="col-num"><span class="num-cell"><span class="num ${prCount ? "is-hot" : "is-zero"}">${prCount}</span>${dots}</span></td>
        <td class="col-num"><span class="num ${repo.open_issue_count ? "is-hot" : "is-zero"}">${repo.open_issue_count}</span></td>
        <td class="col-alerts">${alertsCell}</td>
        <td class="col-ci">
          <div class="ci ci--${esc(ci.state)}">
            ${ci.state === "skipped" ? "" : runsMarkup(ci.runs, runsPerRepo)}
            <span class="ci__label ${longRunning ? "ci__label--slow" : ""}">${longRunning ? ICONS.clock : CI_ICON[ci.state] || ""}${esc(ciLabel)}</span>
          </div>
        </td>
        <td class="col-pushed" title="${esc(I18N.formatDateTime(repo.pushed_at))}">
          <div class="pushed__time">${esc(I18N.formatRelative(repo.pushed_at))}</div>
          ${commitLine}
        </td>
        <td class="col-toggle">
          <button type="button" class="toggle" aria-expanded="${open}" aria-label="${esc(t(open ? "collapse" : "expand"))}">${ICONS.chevron}</button>
        </td>
      </tr>
      ${open ? detailRow(item) : ""}`;
  }

  function detailRow(item) {
    const repo = item.repository;
    const ci = item.ci;

    const prs = visiblePrs(repo);
    const prCount = displayedPrCount(repo);
    const prList = prs.length
      ? `<ul>${prs
          .map(
            (p) => `<li>
              <span class="id">#${p.number}</span>
              <a class="title" href="${esc(p.url)}" target="_blank" rel="noopener" title="${esc(p.title)}">${esc(p.title)}</a>
              ${p.head_branch ? `<span class="branch mono">${esc(p.head_branch)}</span>` : ""}
              ${stateChipMarkup(p)}
              ${p.is_bot ? botTagMarkup() : ""}
              ${p.stale ? staleTagMarkup() : ""}
              ${p.is_draft ? `<span class="tag">${esc(t("draft"))}</span>` : ""}
              <span class="by">${esc(p.author || "")}</span>
            </li>`
          )
          .join("")}</ul>` +
        (prCount > prs.length
          ? `<a class="more" href="${esc(repo.url)}/pulls" target="_blank" rel="noopener">${esc(t("more_on_github", { n: prCount - prs.length }))}</a>`
          : "")
      : `<p class="empty">${esc(t("none_open"))}</p>`;

    const issueList = repo.issues.length
      ? `<ul>${repo.issues
          .map(
            (i) => `<li>
              <span class="id">#${i.number}</span>
              <a class="title" href="${esc(i.url)}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title)}</a>
              ${i.stale ? staleTagMarkup() : ""}
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
              <span class="status">${esc(t("run_" + r.status))} · ${esc(I18N.formatRelative(r.updated_at))}${r.long_running ? " " + esc(t("run_long_running_suffix")) : ""}</span>
            </li>`
          )
          .join("")}</ul>` +
        `<a class="more" href="${esc(repo.url)}/actions" target="_blank" rel="noopener">${esc(t("open_on_github"))}</a>`
      : `<p class="empty">${esc(t("ci_" + ci.state))}</p>`;

    const release = item.release;
    const releaseList = release
      ? (() => {
          const tagLink = `<a class="title mono" href="${esc(release.url)}" target="_blank" rel="noopener">${esc(release.tag)}</a>`;
          const prerelease = release.is_prerelease ? `<span class="tag">${esc(t("prerelease_tag"))}</span>` : "";
          const time = `<span class="by" title="${esc(I18N.formatDateTime(release.published_at))}">${esc(I18N.formatRelative(release.published_at))}</span>`;
          let unreleased;
          if (release.unreleased_commits == null) {
            unreleased = `<span class="state-chip state-chip--neutral">${ICONS.dash}${esc(t("unreleased_unknown"))}</span>`;
          } else if (release.unreleased_commits > 0) {
            unreleased = `<span class="state-chip state-chip--warning">${ICONS.clock}${esc(t("unreleased_commits", { n: release.unreleased_commits }))}</span>`;
          } else {
            unreleased = `<span class="state-chip state-chip--good">${ICONS.check}${esc(t("unreleased_none"))}</span>`;
          }
          return `<ul><li>${tagLink}${prerelease}${time}</li><li>${unreleased}</li></ul>`;
        })()
      : `<p class="empty">${esc(t("release_none"))}</p>`;

    const securityList = `<ul>${securityLines(item.security)
      .map((line) => `<li>${esc(line)}</li>`)
      .join("")}</ul><a class="more" href="${esc(repo.url)}/security" target="_blank" rel="noopener">${esc(t("security_open_link"))}</a>`;

    return `
      <tr class="detail-row" data-detail="${esc(repo.full_name)}">
        <td colspan="7">
          <div class="details">
            <div><h4>${esc(t("details_prs"))} · ${prCount}</h4>${prList}</div>
            <div><h4>${esc(t("details_issues"))} · ${repo.open_issue_count}</h4>${issueList}</div>
            <div><h4>${esc(t("details_runs"))}</h4>${runList}</div>
            <div><h4>${esc(t("details_release"))}</h4>${releaseList}</div>
            <div><h4>${esc(t("details_security"))}</h4>${securityList}</div>
          </div>
        </td>
      </tr>`;
  }

  function renderTable() {
    const d = state.data;
    if (!d) {
      els.rows.innerHTML = `<tr><td colspan="7" class="table-empty">${esc(t("loading"))}</td></tr>`;
      els.count.textContent = "";
      return;
    }
    const items = visibleRepos();
    els.count.textContent = t("repo_count", { shown: items.length, total: d.repos.length });
    els.rows.innerHTML = items.length
      ? items.map(repoRow).join("")
      : `<tr><td colspan="7" class="table-empty">${esc(t("empty_table"))}</td></tr>`;

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
    renderInbox();
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
    for (const [key, el] of [
      ["attention", els.attention],
      ["archived", els.archived],
      ["forks", els.forks],
      ["bots", els.bots],
    ]) {
      el.addEventListener("change", () => {
        state.filters[key] = el.checked;
        if (key === "bots") renderKpis();
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
