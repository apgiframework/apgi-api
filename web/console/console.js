(function () {
  "use strict";

  var state = { token: null, refresh: null, user: null };
  var main = document.getElementById("main");
  var nav = document.getElementById("nav");
  var signout = document.getElementById("signout");

  // ---- DOM helpers (textContent only: API data is never parsed as HTML) ----
  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "class") el.className = attrs[k];
      else if (k.indexOf("on") === 0) el.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) el.setAttribute(k, attrs[k]);
    });
    [].concat(children === undefined ? [] : children).forEach(function (c) {
      if (c === null || c === undefined) return;
      el.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
    });
    return el;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function show(el) { clear(main); main.appendChild(el); main.focus(); }

  // ---- formatting: null is always "unknown", never zero or blank ----
  function isNull(v) { return v === null || v === undefined; }
  function val(v, fmt) {
    if (isNull(v)) return h("span", { class: "val unknown" }, "unknown");
    return h("span", { class: "val" }, fmt ? fmt(v) : v);
  }
  function pct(v) { return (v * 100).toFixed(1) + "%"; }
  function dur(s) {
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
    return Math.floor(s / 86400) + "d";
  }
  function kv(label, node) { return h("div", { class: "kv" }, [h("span", {}, label), node]); }
  function linkVal(v, href) {
    if (isNull(v)) return val(v);
    return h("a", { href: href, class: "val" }, v);
  }
  function pill(text, cls) { return h("span", { class: "pill " + cls }, text); }
  function when(iso) { return iso ? new Date(iso).toLocaleString() : "unknown"; }

  // ---- API ----
  function api(path, opts, retried) {
    opts = opts || {};
    var headers = { "Accept": "application/json" };
    if (opts.body) headers["Content-Type"] = "application/json";
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    return fetch(path, { method: opts.method || "GET", headers: headers, body: opts.body, credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401 && state.refresh && !retried && path.indexOf("/v1/auth/") !== 0) {
          return refreshToken().then(function () { return api(path, opts, true); });
        }
        return r.text().then(function (t) {
          var body = null;
          try { body = t ? JSON.parse(t) : null; } catch (e) { body = null; }
          if (!r.ok) {
            var err = new Error((body && (body.detail || (body.error && body.error.message))) || ("HTTP " + r.status));
            err.status = r.status;
            throw err;
          }
          return body;
        });
      });
  }
  function refreshToken() {
    return fetch("/v1/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: state.refresh })
    }).then(function (r) {
      if (!r.ok) { logout(); throw new Error("Session expired"); }
      return r.json().then(function (t) { state.token = t.access_token; if (t.refresh_token) state.refresh = t.refresh_token; });
    });
  }
  function logout() {
    state.token = state.refresh = state.user = null;
    nav.hidden = true; signout.hidden = true;
    document.getElementById("who").textContent = "";
    location.hash = "#/login";
    renderLogin();
  }
  signout.addEventListener("click", logout);

  // ---- Login ----
  function renderLogin(message) {
    var u = h("input", { id: "u", type: "text", autocomplete: "username", required: "required" });
    var p = h("input", { id: "p", type: "password", autocomplete: "current-password", required: "required" });
    var m = h("input", { id: "m", type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "8" });
    var err = h("p", { class: "error", role: "alert" }, message || "");
    var form = h("form", { class: "panel login", onsubmit: function (e) {
      e.preventDefault();
      var body = { username: u.value, password: p.value };
      if (m.value) body.mfa_code = m.value;
      api("/v1/auth/login", { method: "POST", body: JSON.stringify(body) }).then(function (t) {
        state.token = t.access_token; state.refresh = t.refresh_token || null; state.user = u.value;
        document.getElementById("who").textContent = u.value;
        nav.hidden = false; signout.hidden = false;
        location.hash = "#/home"; route();
      }).catch(function (x) { err.textContent = "Sign-in failed: " + x.message; });
    } }, [
      h("h1", {}, "Sign in"),
      h("p", { class: "muted" }, "Operator access requires an administrator account. Use your MFA code if enabled."),
      h("label", {}, ["Username or email", u]),
      h("label", {}, ["Password", p]),
      h("label", {}, ["MFA code (if enabled)", m]),
      err,
      h("button", { class: "btn", type: "submit" }, "Sign in")
    ]);
    show(form);
  }

  // ---- Home ----
  function originFilter(current, onHome) {
    return h("select", { id: "origin", onchange: onHome ? function (e) {
      location.hash = "#/home" + (e.target.value ? "?origin=" + e.target.value : "");
    } : null }, [["", "All"], ["synthetic", "Synthetic"], ["human_observed", "Human-observed"], ["unclassified", "Unclassified"]].map(function (o) {
      return h("option", { value: o[0], selected: current === o[0] ? "selected" : null }, o[1]);
    }));
  }
  function freshnessPill(f) {
    var cls = f.state === "fresh" ? "ok" : f.state === "stale" ? "bad" : "unknown";
    var text = f.source.replace(/_/g, " ") + ": " + (f.state === "unknown" ? "unknown" : f.state + " (" + dur(f.age_seconds) + " ago)");
    return pill(text, cls);
  }
  function renderHome(params) {
    var originSel = params.get("origin") || "";
    return api("/v1/ops/home" + (originSel ? "?origin=" + encodeURIComponent(originSel) : "")).then(function (res) {
      var d = res.data;
      var ing = d.ingestion, jobs = d.jobs, q = d.quality, o = d.origin;
      var s = ing.sessions_by_status || {}, up = ing.uploads_by_status || {}, t = jobs.tasks_by_status || {};
      var c = q.completion || {}, gaps = q.missing_events || {};

      var panels = [
        h("section", { class: "panel", "aria-labelledby": "p1" }, [
          h("h2", { id: "p1" }, "1 · Ingestion"),
          kv("Sessions started (24h)", val(ing.sessions_started_24h)),
          kv("Trial events received (24h)", val(ing.trial_events_24h)),
          kv("Sessions in progress", linkVal(isNull(ing.sessions_by_status) ? null : s.in_progress, "#/catalog?kind=session&status=in_progress")),
          kv("Sessions completed", linkVal(isNull(ing.sessions_by_status) ? null : s.completed, "#/catalog?kind=session&status=completed")),
          kv("Chunks pending verification", linkVal(isNull(ing.uploads_by_status) ? null : up.pending, "#/catalog?kind=upload&status=pending")),
          kv("Chunks quarantined / rejected", linkVal(isNull(ing.uploads_by_status) ? null : up.quarantined + up.rejected, "#/failures"))
        ]),
        h("section", { class: "panel", "aria-labelledby": "p2" }, [
          h("h2", { id: "p2" }, "2 · Jobs and queues"),
          kv("Queue backend", h("span", { class: "val" }, jobs.queue_backend)),
          kv("Pending", linkVal(isNull(jobs.tasks_by_status) ? null : t.pending, "#/catalog?kind=task&status=pending")),
          kv("Running", linkVal(isNull(jobs.tasks_by_status) ? null : t.running, "#/catalog?kind=task&status=running")),
          kv("Failed", linkVal(isNull(jobs.tasks_by_status) ? null : t.failed, "#/failures")),
          kv("Oldest pending", val(jobs.oldest_pending_age_seconds === null && t.pending === 0 ? 0 : jobs.oldest_pending_age_seconds, dur)),
          kv("Durable jobs lost or stale", linkVal(isNull(jobs.durable_jobs) ? null : jobs.durable_jobs.lease_expired + jobs.durable_jobs.stale_queued, "#/failures")),
          kv("Dead-lettered (retries exhausted)", linkVal(isNull(jobs.durable_jobs) ? null : jobs.durable_jobs.dead, "#/failures")),
          kv("Stuck running", linkVal(jobs.stuck_running, "#/failures")),
          kv("Webhook deliveries failed", linkVal(jobs.webhook_failed, "#/failures"))
        ]),
        h("section", { class: "panel", "aria-labelledby": "p3" }, [
          h("h2", { id: "p3" }, "3 · Data origin"),
          kv("Synthetic", linkVal(o.synthetic, "#/catalog?origin=synthetic")),
          kv("Human-observed", linkVal(o.human_observed, "#/catalog?origin=human_observed")),
          kv("Unclassified", linkVal(o.unclassified, "#/catalog?origin=unclassified")),
          h("p", { class: "muted" }, o.note)
        ]),
        h("section", { class: "panel scientific", "aria-labelledby": "p4" }, [
          h("h2", { id: "p4" }, "4 · Scientific quality"),
          h("p", { class: "muted" }, "Research view, kept separate from operations. " + q.indicators_available + " of " + q.indicators_planned + " planned indicators are measurable today."),
          kv("Completion rate", val(c.completion_rate, pct)),
          kv("Interruption (abandoned) rate", val(c.interruption_rate, pct)),
          kv("Sessions with missing events", val(gaps.sessions_with_gaps)),
          kv("Missing events (sequence gaps)", val(gaps.missing_events)),
          kv("Device tier distribution", val(q.device_tier_distribution === null ? null : !Object.keys(q.device_tier_distribution).length ? "none recorded" : Object.keys(q.device_tier_distribution).sort().map(function (k) { return k + ":" + q.device_tier_distribution[k]; }).join("  "))),
        ]),
        h("section", { class: "panel", "aria-labelledby": "p5" }, [
          h("h2", { id: "p5" }, "5 · Retention and deletion"),
          kv("Purges overdue", val(d.retention_and_deletion.overdue_purges)),
          kv("Expiring within 30 days", val(d.retention_and_deletion.expiring_30d)),
          h("p", { class: "muted" }, "Source: " + d.retention_and_deletion.source)
        ]),
        h("section", { class: "panel", "aria-labelledby": "p6" }, [
          h("h2", { id: "p6" }, "6 · Platform health"),
          kv("Request success, last hour (non-5xx)", val(d.infrastructure.success_ratio_1h, pct)),
          kv("Requests, last hour", val(d.infrastructure.request_count_1h)),
          kv("p95 latency (worst instance)", val(d.infrastructure.p95_latency_ms, function (v) { return v + " ms"; })),
          kv("5xx ratio", val(d.infrastructure.error_ratio_5xx, pct)),
          kv("Database round-trip", val(d.infrastructure.database_latency_ms, function (v) { return v + " ms"; })),
          kv("Celery workers online", val(d.infrastructure.celery_workers_online)),
          kv("Last successful backup", val(d.infrastructure.last_backup_at, when)),
          kv("Last restore drill", val(d.infrastructure.last_restore_drill_at, when)),
          h("p", { class: "muted" }, d.infrastructure.source),
          d.infrastructure.errors.length ? h("p", { class: "error", role: "alert" }, "Unreadable: " + d.infrastructure.errors.join("; ")) : null
        ])
      ];

      var anyStale = d.freshness.some(function (f) { return f.state === "stale"; });
      var unclassified = o.unclassified;
      show(h("div", {}, [
        h("h1", {}, "Home"),
        h("p", { class: "muted" }, "Values come straight from the API. Anything that cannot be measured is shown as unknown."),
        h("label", {}, ["Data origin (panels 1 and 4)", originFilter(originSel, true)]),
        res.data.origin_filter ? h("div", { class: "banner", role: "status" }, "Panels 1 and 4 show " + res.data.origin_filter.replace("_", "-") + " sessions only.") : null,
        h("div", { class: "fresh", "aria-label": "Source freshness" }, [h("strong", {}, "Freshness:")].concat(d.freshness.map(freshnessPill))),
        d.alerts_open ? h("div", { class: "banner", role: "status" }, [d.alerts_open + " open alert(s). ", h("a", { href: "#/alerts" }, "Review")]) : null,
        anyStale ? h("div", { class: "banner", role: "status" }, "One or more sources are stale. Check the Failures screen before trusting the counts below.") : null,
        unclassified ? h("div", { class: "banner", role: "status" }, unclassified + " session(s) have no recorded origin and are not assumed human or synthetic.") : null,
        h("div", { class: "grid" }, panels),
        h("button", { class: "btn ghost", onclick: route }, "Refresh")
      ]));
    });
  }

  // ---- Catalog ----
  function renderCatalog(params) {
    var kindSel = h("select", { id: "kind" }, [["", "All"], ["session", "Sessions"], ["upload", "Chunks"], ["task", "Jobs"]].map(function (o) {
      return h("option", { value: o[0], selected: params.get("kind") === o[0] ? "selected" : null }, o[1]);
    }));
    var statusIn = h("input", { id: "status", value: params.get("status") || "", placeholder: "e.g. completed" });
    var originIn = originFilter(params.get("origin") || "");
    var qIn = h("input", { id: "q", type: "search", value: params.get("q") || "", placeholder: "Session or upload ID" });
    var page = parseInt(params.get("page") || "1", 10);
    var results = h("div", {});
    var form = h("form", { class: "filters", role: "search", onsubmit: function (e) {
      e.preventDefault();
      var sp = new URLSearchParams();
      if (kindSel.value) sp.set("kind", kindSel.value);
      if (statusIn.value) sp.set("status", statusIn.value);
      if (qIn.value) sp.set("q", qIn.value);
      if (originIn.value) sp.set("origin", originIn.value);
      location.hash = "#/catalog" + (sp.toString() ? "?" + sp.toString() : "");
    } }, [
      h("label", {}, ["Search", qIn]), h("label", {}, ["Type", kindSel]), h("label", {}, ["Origin", originIn]), h("label", {}, ["Status", statusIn]),
      h("button", { class: "btn", type: "submit" }, "Apply")
    ]);
    show(h("div", {}, [h("h1", {}, "Catalog"),
      h("p", { class: "muted" }, "Derived from sessions, chunk uploads and jobs. The dedicated asset catalog is not deployed yet."),
      form, results]));

    var sp = new URLSearchParams(params); sp.set("page", page);
    return api("/v1/ops/catalog?" + sp.toString()).then(function (res) {
      var rows = res.data.items.map(function (i) {
        var idCell = i.kind === "session" ? h("a", { href: "#/session/" + encodeURIComponent(i.id) }, i.id) : i.id;
        return h("tr", {}, [h("td", {}, i.kind), h("td", {}, idCell), h("td", {}, i.status),
          h("td", {}, i.device_tier || "—"), h("td", {}, i.origin ? i.origin.replace("_", "-") : (i.kind === "session" ? "unclassified" : "n/a")), h("td", {}, when(i.created_at))]);
      });
      var prev = page > 1 ? h("a", { href: pageHref(params, page - 1) }, "‹ Previous") : null;
      var next = page * 25 < res.data.total ? h("a", { href: pageHref(params, page + 1) }, "Next ›") : null;
      results.appendChild(h("div", { class: "tablewrap" }, h("table", {}, [
        h("thead", {}, h("tr", {}, ["Type", "ID", "Status", "Tier", "Origin", "Created"].map(function (x) { return h("th", { scope: "col" }, x); }))),
        h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: "6" }, "No matching records.")))
      ])));
      results.appendChild(h("p", { class: "muted" }, [res.data.total + " records  ", prev, " ", next]));
    });
  }
  function pageHref(params, page) {
    var sp = new URLSearchParams(params); sp.set("page", page);
    return "#/catalog?" + sp.toString();
  }

  function renderSession(id) {
    return api("/v1/ops/catalog/session/" + encodeURIComponent(id)).then(function (res) {
      var d = res.data;
      show(h("div", {}, [
        h("p", {}, h("a", { href: "#/catalog" }, "‹ Catalog")),
        h("h1", {}, "Session " + d.id),
        h("div", { class: "panel" }, [
          kv("Status", h("span", { class: "val" }, d.status)),
          kv("Study", h("span", { class: "val" }, d.study_id)),
          kv("Device tier", val(d.device_tier)),
          kv("Origin", val(d.origin === null ? null : d.origin.replace("_", "-"))),
          kv("Correlation ID", val(d.correlation_id)),
          kv("Norm eligible", val(d.norm_eligible, String)),
          kv("Trial events", val(d.trial_event_count)),
          kv("Scored", val(d.scored, function (v) { return v ? "yes" : "no"; }))
        ]),
        h("h2", {}, "Pipeline timeline"),
        h("ol", { class: "timeline" }, d.events.map(function (e) {
          return h("li", {}, [h("span", { class: "muted" }, when(e.at) + "  "), e.event, e.detail ? " — " + e.detail : ""]);
        }))
      ]));
    });
  }

  // ---- Failures ----
  function renderFailures() {
    return api("/v1/ops/failures").then(function (res) {
      var groups = {};
      res.data.forEach(function (f) { (groups[f.group] = groups[f.group] || []).push(f); });
      var names = Object.keys(groups);
      var out = [h("h1", {}, "Failures"),
        h("p", { class: "muted" }, "Failures are detected from stored state, not from handlers reporting their own crashes. Correlation IDs are recorded for sessions created after this release; use them to search Cloud Logging.")];
      if (!names.length) out.push(h("div", { class: "panel" }, [pill("clear", "ok"), " No failures detected in the monitored sources."]));
      names.forEach(function (g) {
        var rows = groups[g].map(function (f) {
          var status = h("span", { role: "status" });
          var btn = f.retry_eligible ? h("button", { class: "btn", onclick: function () {
            btn.disabled = true; status.textContent = " retrying…";
            api("/v1/ops/jobs/" + encodeURIComponent(f.job_id) + ":retry", { method: "POST" })
              .then(function () { status.textContent = " re-queued"; })
              .catch(function (x) { btn.disabled = false; status.textContent = " " + x.message; });
          } }, "Retry") : h("span", { class: "muted" }, "Not retryable");
          var ref = f.kind === "session" ? h("a", { href: "#/session/" + encodeURIComponent(f.ref) }, f.ref) : f.ref;
          return h("tr", {}, [h("td", {}, ref), h("td", {}, f.correlation_id || "not recorded"), h("td", {}, f.owner_role), h("td", {}, val(f.age_seconds, dur)),
            h("td", {}, f.error || "No error recorded"), h("td", {}, [btn, status])]);
        });
        out.push(h("h2", {}, g + " (" + groups[g].length + ")"));
        out.push(h("div", { class: "tablewrap" }, h("table", {}, [
          h("thead", {}, h("tr", {}, ["Reference", "Correlation ID", "Owner", "Age", "Error", "Action"].map(function (x) { return h("th", { scope: "col" }, x); }))),
          h("tbody", {}, rows)
        ])));
      });
      show(h("div", {}, out));
    });
  }

  // ---- Alerts ----
  function renderAlerts() {
    return api("/v1/ops/alerts").then(function (res) {
      var sevCls = { critical: "bad", error: "bad", warning: "warn", info: "unknown" };
      var rows = res.data.map(function (a) {
        var status = h("span", { role: "status" });
        var action = a.status === "open" ? h("button", { class: "btn", onclick: function (e) {
          e.target.disabled = true;
          api("/v1/ops/alerts/" + encodeURIComponent(a.alert_id) + ":ack", { method: "POST" })
            .then(route).catch(function (x) { e.target.disabled = false; status.textContent = " " + x.message; });
        } }, "Acknowledge") : h("span", { class: "muted" }, a.status === "acknowledged" ? "Acknowledged by " + a.acknowledged_by : "Resolved " + when(a.resolved_at));
        return h("tr", {}, [h("td", {}, pill(a.severity, sevCls[a.severity] || "unknown")), h("td", {}, a.title),
          h("td", {}, a.detail), h("td", {}, when(a.opened_at)),
          h("td", {}, a.status + (a.notified ? "" : " (not delivered)")), h("td", {}, [action, status])]);
      });
      show(h("div", {}, [h("h1", {}, "Alerts"),
        h("p", { class: "muted" }, "One alert per standing condition; a recovery notice is sent when it clears. Delivered by the configured channels (log, webhook, email)."),
        rows.length ? h("div", { class: "tablewrap" }, h("table", {}, [
          h("thead", {}, h("tr", {}, ["Severity", "Alert", "Detail", "Opened", "State", "Action"].map(function (x) { return h("th", { scope: "col" }, x); }))),
          h("tbody", {}, rows)])) : h("div", { class: "panel" }, [pill("clear", "ok"), " No open or recent alerts."])]));
    });
  }

  // ---- Router ----
  function route() {
    if (!state.token) return renderLogin();
    var hash = location.hash.replace(/^#/, "") || "/home";
    var qi = hash.indexOf("?");
    var path = qi < 0 ? hash : hash.slice(0, qi);
    var params = new URLSearchParams(qi < 0 ? "" : hash.slice(qi + 1));
    var name = path.split("/")[1] || "home";
    [].forEach.call(nav.querySelectorAll("a"), function (a) {
      if (a.dataset.route === name || (name === "session" && a.dataset.route === "catalog")) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    var p;
    if (name === "catalog") p = renderCatalog(params);
    else if (name === "session") p = renderSession(decodeURIComponent(path.split("/")[2] || ""));
    else if (name === "failures") p = renderFailures();
    else if (name === "alerts") p = renderAlerts();
    else p = renderHome(params);
    p.catch(function (e) {
      if (e.status === 403) show(h("div", { class: "panel" }, [h("h1", {}, "Access denied"), h("p", {}, "The console requires an administrator role.")]));
      else if (e.status !== 401) show(h("div", { class: "panel" }, [h("h1", {}, "Could not load"), h("p", { class: "error", role: "alert" }, e.message), h("button", { class: "btn", onclick: route }, "Retry")]));
    });
  }
  window.addEventListener("hashchange", route);
  route();
})();
