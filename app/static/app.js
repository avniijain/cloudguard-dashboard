(function () {
  "use strict";

  const REFRESH_MS = 60000;
  const DEFAULT_BRANCH = "main";
  const repoSelect = document.getElementById("repo-select");
  const limitSelect = document.getElementById("limit-select");
  const refreshBtn = document.getElementById("refresh-btn");
  const banner = document.getElementById("banner");
  const statusEl = document.getElementById("status");
  const summaryDecision = document.getElementById("summary-decision");
  const summaryCounts = document.getElementById("summary-counts");
  const summaryScore = document.getElementById("summary-score");
  const runsBody = document.getElementById("runs-body");
  const deploymentsBody = document.getElementById("deployments-body");
  const rollbacksBody = document.getElementById("rollbacks-body");
  const deploymentsStatus = document.getElementById("deployments-status");
  const deploymentsError = document.getElementById("deployments-error");

  let inFlight = false;
  let timerId = null;

  function showError(message) {
    banner.hidden = false;
    banner.textContent = message;
  }

  function clearError() {
    banner.hidden = true;
    banner.textContent = "";
  }

  function setStatus(message) {
    statusEl.textContent = message;
  }

  function apiMessage(payload, fallback) {
    if (!payload || typeof payload !== "object") {
      return fallback;
    }
    const detail = payload.detail;
    if (typeof detail === "string" && detail) {
      return detail;
    }
    if (Array.isArray(detail) && detail.length) {
      const parts = [];
      for (const item of detail) {
        if (typeof item === "string") {
          parts.push(item);
        } else if (item && typeof item.msg === "string") {
          parts.push(item.msg);
        }
      }
      if (parts.length) {
        return parts.join("; ");
      }
    }
    return fallback;
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    let payload = null;
    const contentType = response.headers.get("content-type") || "";
    if (contentType.indexOf("application/json") !== -1) {
      try {
        payload = await response.json();
      } catch (err) {
        payload = null;
      }
    }
    if (!response.ok) {
      throw new Error(
        apiMessage(payload, "Request failed with HTTP " + response.status)
      );
    }
    if (payload === null) {
      throw new Error("The server did not return JSON");
    }
    return payload;
  }

  function clearNode(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function normalizeDecision(value) {
    if (typeof value !== "string" || !value) {
      return "";
    }
    return value.trim().toUpperCase();
  }

  function badgeFor(decision) {
    const span = document.createElement("span");
    span.className = "badge";
    const key = normalizeDecision(decision);
    if (key === "ALLOW") {
      span.className += " badge-allow";
      span.textContent = "ALLOW";
    } else if (key === "WARN") {
      span.className += " badge-warn";
      span.textContent = "WARN";
    } else if (key === "BLOCK") {
      span.className += " badge-block";
      span.textContent = "BLOCK";
    } else {
      span.className += " badge-none";
      span.textContent = "NO SCAN";
    }
    return span;
  }

  function relativeTime(iso) {
    if (!iso) {
      return { label: "—", title: "" };
    }
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return { label: String(iso), title: String(iso) };
    }
    const title = date.toLocaleString();
    const deltaSec = Math.round((Date.now() - date.getTime()) / 1000);
    const future = deltaSec < 0;
    const abs = Math.abs(deltaSec);
    let label;
    if (abs < 45) {
      label = "just now";
    } else if (abs < 90) {
      label = "1 min ago";
    } else if (abs < 3600) {
      label = Math.round(abs / 60) + " min ago";
    } else if (abs < 5400) {
      label = "1 hour ago";
    } else if (abs < 86400) {
      label = Math.round(abs / 3600) + " hours ago";
    } else if (abs < 172800) {
      label = "1 day ago";
    } else {
      label = Math.round(abs / 86400) + " days ago";
    }
    if (future && abs >= 45) {
      label = label.replace(" ago", " from now");
    }
    return { label: label, title: title };
  }

  function severityLabel(scan) {
    if (!scan || !scan.counts || !scan.counts.by_severity) {
      return "—";
    }
    const sev = scan.counts.by_severity;
    const c = Number(sev.critical || 0);
    const h = Number(sev.high || 0);
    const m = Number(sev.medium || 0);
    const l = Number(sev.low || 0);
    return c + "/" + h + "/" + m + "/" + l;
  }

  function sortNewestFirst(runs) {
    return runs.slice().sort(function (a, b) {
      const ta = Date.parse(a && a.created_at) || 0;
      const tb = Date.parse(b && b.created_at) || 0;
      return tb - ta;
    });
  }

  function updateSummary(runs) {
    const newest = sortNewestFirst(runs);
    let latestMain = "";
    for (let i = 0; i < newest.length; i += 1) {
      const run = newest[i];
      if (run && run.branch === DEFAULT_BRANCH && run.scan && run.scan.decision) {
        latestMain = normalizeDecision(run.scan.decision);
        break;
      }
    }
    clearNode(summaryDecision);
    if (latestMain) {
      summaryDecision.appendChild(badgeFor(latestMain));
    } else {
      summaryDecision.textContent = "No scan on main";
    }

    let allow = 0;
    let warn = 0;
    let block = 0;
    for (let i = 0; i < runs.length; i += 1) {
      const decision = normalizeDecision(
        runs[i] && runs[i].scan ? runs[i].scan.decision : ""
      );
      if (decision === "ALLOW") {
        allow += 1;
      } else if (decision === "WARN") {
        warn += 1;
      } else if (decision === "BLOCK") {
        block += 1;
      }
    }
    summaryCounts.textContent =
      "ALLOW " + allow + " · WARN " + warn + " · BLOCK " + block;

    let score = "—";
    for (let i = 0; i < newest.length; i += 1) {
      const scan = newest[i] && newest[i].scan;
      if (scan && scan.risk_score !== null && scan.risk_score !== undefined) {
        score = String(scan.risk_score);
        break;
      }
    }
    summaryScore.textContent = score;
  }

  function runLink(url) {
    if (typeof url === "string" && url.indexOf("https://github.com/") === 0) {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.textContent = "Open run";
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.addEventListener("click", function (event) {
        event.stopPropagation();
      });
      return anchor;
    }
    const span = document.createElement("span");
    span.textContent = url ? String(url) : "—";
    return span;
  }

  function detailsContent(run) {
    const wrap = document.createElement("div");
    const reasons =
      run.scan && Array.isArray(run.scan.reasons) ? run.scan.reasons : [];
    if (reasons.length) {
      const why = document.createElement("p");
      why.className = "why-line";
      const translated = [];
      for (let i = 0; i < reasons.length; i += 1) {
        const reason = String(reasons[i]);
        const match = reason.match(/^(RISK_SCORE_AT_OR_ABOVE_(WARN|BLOCK)_THRESHOLD):(.+)$/);
        translated.push(match
          ? "Risk score is at or above the " + match[2] + " threshold (" + match[3] + ")"
          : reason);
      }
      why.textContent = "Why: " + translated.join("; ");
      wrap.appendChild(why);
    } else {
      const why = document.createElement("p");
      why.className = "why-line";
      why.textContent = "Why: No reasons reported.";
      wrap.appendChild(why);
    }

    const scan = run.scan || {};
    const findings = Array.isArray(scan.findings) ? scan.findings : [];
    const heading = document.createElement("strong");
    heading.textContent = "Findings";
    wrap.appendChild(heading);
    if (findings.length) {
      const scroll = document.createElement("div");
      scroll.className = "findings-scroll";
      scroll.tabIndex = 0;
      scroll.setAttribute("role", "region");
      scroll.setAttribute("aria-label", "Scan findings");
      const table = document.createElement("table");
      table.className = "findings-table";
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      ["Severity", "Type", "Rule or CVE", "Package", "Target", "Points"].forEach(function (label) {
        const th = document.createElement("th");
        th.scope = "col";
        th.textContent = label;
        headRow.appendChild(th);
      });
      head.appendChild(headRow);
      table.appendChild(head);
      const body = document.createElement("tbody");
      findings.forEach(function (finding) {
        const row = document.createElement("tr");
        const cells = [
          finding.severity, finding.type,
          [finding.rule_id, finding.cve].filter(Boolean).join(" / "),
          [finding.package, finding.installed_version ? "(" + finding.installed_version +
            (finding.fixed_version ? " → " + finding.fixed_version : "") + ")" : ""].filter(Boolean).join(" "),
          Array.isArray(finding.targets) ? finding.targets.join(", ") : null,
          finding.risk_score,
        ];
        cells.forEach(function (value) {
          const td = document.createElement("td");
          td.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
          row.appendChild(td);
        });
        body.appendChild(row);
      });
      table.appendChild(body);
      scroll.appendChild(table);
      wrap.appendChild(scroll);
    } else {
      const none = document.createElement("p");
      none.textContent = "No findings reported.";
      wrap.appendChild(none);
    }
    if (typeof scan.findings_total === "number" && scan.findings_total > findings.length) {
      const count = document.createElement("p");
      count.className = "finding-count";
      count.textContent = "Showing " + findings.length + " of " + scan.findings_total;
      wrap.appendChild(count);
    }

    const sonar = document.createElement("p");
    sonar.className = "sonar-status";
    sonar.textContent = "Sonar status: " + (typeof scan.sonar_status === "string" ? scan.sonar_status : "Not configured");
    wrap.appendChild(sonar);

    if (scan.degraded === true) {
      const notice = document.createElement("p");
      notice.className = "degraded-notice";
      notice.textContent = "Data degraded";
      if (Array.isArray(scan.degraded_reasons) && scan.degraded_reasons.length) {
        notice.textContent += ": " + scan.degraded_reasons.map(String).join("; ");
      }
      wrap.appendChild(notice);
    }

    if (run.scan_error) {
      const err = document.createElement("p");
      err.className = "scan-error";
      err.textContent = "Scan error: " + String(run.scan_error);
      wrap.appendChild(err);
    }
    return wrap;
  }

  function toggleDetails(row, detailsRow) {
    const open = row.getAttribute("aria-expanded") === "true";
    row.setAttribute("aria-expanded", open ? "false" : "true");
    detailsRow.hidden = open;
  }

  function renderRuns(runs) {
    clearNode(runsBody);
    const newest = sortNewestFirst(runs);
    if (!newest.length) {
      const emptyRow = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 9;
      cell.textContent = "No workflow runs found.";
      emptyRow.appendChild(cell);
      runsBody.appendChild(emptyRow);
      return;
    }

    for (let i = 0; i < newest.length; i += 1) {
      const run = newest[i] || {};
      const scan = run.scan;
      const row = document.createElement("tr");
      row.className = "run-row";
      row.tabIndex = 0;
      row.setAttribute("aria-expanded", "false");

      const detailsRow = document.createElement("tr");
      detailsRow.className = "details-row";
      detailsRow.hidden = true;
      const detailsCell = document.createElement("td");
      detailsCell.colSpan = 9;
      detailsCell.appendChild(detailsContent(run));
      detailsRow.appendChild(detailsCell);

      const timeInfo = relativeTime(run.created_at);
      const cells = [
        timeInfo.label,
        run.branch || "—",
        run.event || "—",
        run.head_sha || "—",
        run.conclusion || run.status || "—",
      ];
      for (let c = 0; c < cells.length; c += 1) {
        const td = document.createElement("td");
        td.textContent = cells[c];
        if (c === 0 && timeInfo.title) {
          td.title = timeInfo.title;
        }
        row.appendChild(td);
      }

      const decisionTd = document.createElement("td");
      decisionTd.appendChild(badgeFor(scan && scan.decision));
      row.appendChild(decisionTd);

      const riskTd = document.createElement("td");
      riskTd.textContent =
        scan && scan.risk_score !== null && scan.risk_score !== undefined
          ? String(scan.risk_score)
          : "—";
      row.appendChild(riskTd);

      const sevTd = document.createElement("td");
      sevTd.textContent = severityLabel(scan);
      row.appendChild(sevTd);

      const linkTd = document.createElement("td");
      linkTd.appendChild(runLink(run.html_url));
      row.appendChild(linkTd);

      row.addEventListener("click", function () {
        toggleDetails(row, detailsRow);
      });
      row.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggleDetails(row, detailsRow);
        }
      });

      runsBody.appendChild(row);
      runsBody.appendChild(detailsRow);
    }
  }

  function addTextCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
    row.appendChild(cell);
    return cell;
  }

  function renderDeployments(payload, repo, runs) {
    clearNode(deploymentsBody);
    clearNode(rollbacksBody);
    const revisions = payload && Array.isArray(payload.revisions) ? payload.revisions : [];
    if (!revisions.length) {
      const row = document.createElement("tr");
      const cell = addTextCell(row, "No revisions found.");
      cell.colSpan = 9;
      deploymentsBody.appendChild(row);
    }
    revisions.forEach(function (revision) {
      const item = revision || {};
      const row = document.createElement("tr");
      const liveCell = document.createElement("td");
      if (item.live === true || item.traffic_weight === 100) {
        const badge = document.createElement("span");
        badge.className = "badge badge-allow live-badge";
        badge.textContent = "LIVE";
        liveCell.appendChild(badge);
        const liveText = document.createElement("span");
        liveText.className = "live-text";
        liveText.textContent = "Live";
        liveCell.appendChild(liveText);
      } else {
        liveCell.textContent = "—";
      }
      row.appendChild(liveCell);
      addTextCell(row, item.name);
      const createdCell = document.createElement("td");
      const created = relativeTime(item.created_time);
      createdCell.textContent = created.label;
      if (created.title) {
        createdCell.title = created.title;
      }
      row.appendChild(createdCell);
      addTextCell(row, item.active === true ? "Yes" : item.active === false ? "No" : "—");
      addTextCell(row, item.traffic_weight === null || item.traffic_weight === undefined
        ? "—" : String(item.traffic_weight) + "%");
      addTextCell(row, item.health_state);
      const commitCell = document.createElement("td");
      if (typeof item.commit_sha === "string" && /^[0-9a-f]{7}$/.test(item.commit_sha)) {
        const link = document.createElement("a");
        link.href = "https://github.com/" + repo + "/commit/" + item.commit_sha;
        link.textContent = item.commit_sha;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        commitCell.appendChild(link);
      } else {
        commitCell.textContent = "—";
      }
      row.appendChild(commitCell);
      const digest = typeof item.image_digest === "string" && item.image_digest.indexOf("sha256:") === 0
        ? item.image_digest.slice(0, 19) : "—";
      addTextCell(row, digest);
      let decision = "n/a";
      if (typeof item.commit_sha === "string") {
        const matching = runs.find(function (run) {
          return run && run.head_sha === item.commit_sha;
        });
        if (matching && matching.scan && matching.scan.decision) {
          decision = matching.scan.decision;
        }
      }
      addTextCell(row, decision);
      deploymentsBody.appendChild(row);
    });

    const rollbacks = payload && Array.isArray(payload.rollbacks) ? payload.rollbacks : [];
    if (!rollbacks.length) {
      const row = document.createElement("tr");
      const cell = addTextCell(row, "No rollback runs configured or found.");
      cell.colSpan = 4;
      rollbacksBody.appendChild(row);
    }
    rollbacks.forEach(function (rollback) {
      const item = rollback || {};
      const row = document.createElement("tr");
      const timeCell = document.createElement("td");
      const time = relativeTime(item.created_at);
      timeCell.textContent = time.label;
      if (time.title) {
        timeCell.title = time.title;
      }
      row.appendChild(timeCell);
      addTextCell(row, item.actor);
      addTextCell(row, item.conclusion || item.status);
      const runCell = document.createElement("td");
      if (typeof item.html_url === "string" && item.html_url.indexOf("https://github.com/") === 0) {
        const link = document.createElement("a");
        link.href = item.html_url;
        link.textContent = "Open run";
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        runCell.appendChild(link);
      } else {
        runCell.textContent = "—";
      }
      row.appendChild(runCell);
      rollbacksBody.appendChild(row);
    });
  }

  async function loadDeployments(repo, runs) {
    deploymentsError.hidden = true;
    deploymentsError.textContent = "";
    deploymentsStatus.textContent = "Loading deployments…";
    try {
      const slash = repo.indexOf("/");
      const owner = encodeURIComponent(repo.slice(0, slash));
      const name = encodeURIComponent(repo.slice(slash + 1));
      const payload = await fetchJson("/api/repos/" + owner + "/" + name + "/deployments");
      renderDeployments(payload, repo, runs);
      deploymentsStatus.textContent = "Deployments updated " + new Date().toLocaleTimeString();
    } catch (err) {
      deploymentsStatus.textContent = "Unable to load deployments.";
      deploymentsError.hidden = false;
      deploymentsError.textContent = err && err.message ? err.message : "Unable to load deployments.";
    }
  }

  function fillRepos(repos) {
    const previous = repoSelect.value;
    clearNode(repoSelect);
    for (let i = 0; i < repos.length; i += 1) {
      const entry = repos[i] || {};
      const option = document.createElement("option");
      option.value = entry.repo || "";
      option.textContent = entry.repo || "";
      repoSelect.appendChild(option);
    }
    if (previous) {
      repoSelect.value = previous;
    }
  }

  async function loadDashboard() {
    if (inFlight) {
      return;
    }
    inFlight = true;
    refreshBtn.disabled = true;
    setStatus("Loading…");
    try {
      const repos = await fetchJson("/api/repos");
      if (!Array.isArray(repos) || repos.length === 0) {
        fillRepos([]);
        updateSummary([]);
        renderRuns([]);
        throw new Error("No repositories are registered");
      }
      fillRepos(repos);
      const repo = repoSelect.value;
      const slash = repo.indexOf("/");
      if (slash < 1) {
        throw new Error("Selected repository is invalid");
      }
      const owner = repo.slice(0, slash);
      const name = repo.slice(slash + 1);
      const limit = limitSelect.value || "10";
      const runs = await fetchJson(
        "/api/repos/" + owner + "/" + name + "/runs?limit=" + limit
      );
      if (!Array.isArray(runs)) {
        throw new Error("Runs response was not a list");
      }
      clearError();
      updateSummary(runs);
      renderRuns(runs);
      await loadDeployments(repo, runs);
      setStatus("Updated " + new Date().toLocaleTimeString());
    } catch (err) {
      const message = err && err.message ? err.message : "Unable to load dashboard";
      showError(message);
      setStatus("Unable to refresh");
    } finally {
      inFlight = false;
      refreshBtn.disabled = false;
    }
  }

  repoSelect.addEventListener("change", function () {
    loadDashboard();
  });
  limitSelect.addEventListener("change", function () {
    loadDashboard();
  });
  refreshBtn.addEventListener("click", function () {
    loadDashboard();
  });

  timerId = window.setInterval(loadDashboard, REFRESH_MS);
  window.addEventListener("beforeunload", function () {
    if (timerId) {
      window.clearInterval(timerId);
    }
  });

  loadDashboard();
})();
