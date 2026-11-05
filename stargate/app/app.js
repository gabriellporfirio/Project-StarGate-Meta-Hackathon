/* Guided phone setup client. Plain fetch + DOM. Dynamic text (model
   text, quotes, file names) is inserted with textContent only, never
   as HTML. */
"use strict";

const STEPS = ["welcome", "connect", "questions", "phone", "services",
  "research", "build", "boot", "done"];

let CSRF = "";
let STATE = null;
let STEP = "welcome";
let POLL = null;
let BOOT_CACHE = { key: "", text: "" };

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function fmtBytes(n) {
  if (typeof n !== "number") return "unknown";
  if (n < 1024) return n + " B";
  const units = ["B", "KB", "MB", "GB"];
  let v = n, u = 0;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  return v.toFixed(1) + " " + units[u] + " (" + n + " bytes)";
}

async function api(method, path, body) {
  const opts = { method: method, headers: {} };
  if (method === "POST") {
    opts.headers["Content-Type"] = "application/json";
    opts.headers["X-CSRF-Token"] = CSRF;
    opts.body = JSON.stringify(body || {});
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  return { status: res.status, data: data };
}

function setStatus(text) {
  elClear("status-line");
  document.getElementById("status-line").textContent = text;
}

function elClear(id) {
  const node = document.getElementById(id);
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

function warnBox(text) {
  return el("div", "warn", text);
}

function tag(text, kind) {
  return el("span", kind ? "tag " + kind : "tag", text);
}

function detailsBox(title, rows) {
  const box = el("details", "panel");
  box.appendChild(el("summary", null, title));
  rows.forEach(function (row) {
    const p = el("p", "mono faint", row[0] + ": " + row[1]);
    box.appendChild(p);
  });
  return box;
}

function outputDetails(name, view) {
  if (!view || view.output_sha256 === null) return null;
  const rows = [["output", view.output_sha256 || "none"]];
  const inputs = view.input_hashes || {};
  Object.keys(inputs).sort().forEach(function (k) {
    rows.push(["input " + k, inputs[k] || "none"]);
  });
  rows.push(["job", view.job_id || "none"]);
  return detailsBox(name + " details", rows);
}

function staleBox(view, rerunStep) {
  const box = el("div", "panel");
  box.appendChild(el("h2", null, "Out of date"));
  box.appendChild(el("p", "muted", view.message ||
    "Out of date: re-run to refresh."));
  const btn = el("button", "primary", "Re-run");
  btn.addEventListener("click", function () { gotoStep(rerunStep); });
  box.appendChild(btn);
  return box;
}

function gotoStep(step) {
  if (POLL) { clearInterval(POLL); POLL = null; }
  document.getElementById("screen").classList.remove("loading");
  STEP = step;
  render();
  document.getElementById("screen").focus({ preventScroll: true });
}

/* -- step screens ------------------------------------------------ */

function screenWelcome(root) {
  const hero = el("div", "panel");
  hero.appendChild(el("h2", null,
    "Turn the phone in your drawer into something useful."));
  hero.appendChild(el("p", "muted",
    "Plug it in. We check what it can do, suggest what it could run, " +
    "research how to do it safely, then build a clean system made for it."));
  const row = el("div", "row");
  ["1 Check", "2 Suggest", "3 Research", "4 Build and install"].forEach(
    function (t) { row.appendChild(tag(t)); });
  hero.appendChild(row);
  hero.appendChild(el("p", "faint",
    "About 20 minutes. Nothing changes on your phone until you say so."));
  const btn = el("button", "primary", "Start");
  btn.addEventListener("click", function () { gotoStep("connect"); });
  hero.appendChild(btn);
  root.appendChild(hero);
}

function screenConnect(root) {
  const st = STATE;
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "Connect your phone"));
  const row = el("div", "row");
  const label = el("label", null, "Simulated phone: ");
  const sel = document.createElement("select");
  sel.id = "profile-pick";
  ["flagship", "budget", "old32", "locked"].forEach(function (name) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (st.profile && st.profile.name === name) opt.selected = true;
    sel.appendChild(opt);
  });
  label.appendChild(sel);
  row.appendChild(label);
  const btn = el("button", "primary", "Check this phone");
  btn.addEventListener("click", onConnect);
  row.appendChild(btn);
  panel.appendChild(row);
  if (st.profile) {
    const tags = el("p", null);
    tags.appendChild(tag("SIMULATED profile"));
    tags.appendChild(tag(st.profile.name, "indigo"));
    panel.appendChild(tags);
  }
  root.appendChild(panel);

  const help = el("div", "panel");
  help.appendChild(el("h2", null, "How to connect"));
  const list = el("ul", "plain");
  ["Use a data cable. Some cables only charge.",
   "Turn on USB debugging: Settings, About phone, tap Build number " +
   "7 times, then Developer options.",
   "Unlock the screen and tap Allow."].forEach(function (t) {
    list.appendChild(el("li", null, t));
  });
  help.appendChild(list);
  help.appendChild(el("p", "muted",
    "Brand-specific steps: unknown (no recorded research yet)."));
  root.appendChild(help);
  root.appendChild(warnBox(
    "Connect a real phone: not in this demo. Use a phone you do not " +
    "need any more; installing later will erase it."));
}

async function onConnect() {
  const sel = document.getElementById("profile-pick");
  setStatus("Checking the phone.");
  const res = await api("POST", "/api/connect", { profile: sel.value });
  if (res.status !== 200 || !res.data) {
    setStatus("Check failed.");
    showError((res.data && res.data.error) || "request failed");
    return;
  }
  STATE = res.data.state;
  setStatus("Phone checked.");
  render();
}

function screenQuestions(root) {
  const st = STATE;
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "A few quick questions"));
  st.questions.forEach(function (q) {
    panel.appendChild(el("p", null, q.text));
    const saved = st.answers && st.answers.data[q.id];
    q.options.forEach(function (opt) {
      const value = opt[0], labelText = opt[1];
      const label = el("label", "choice");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = q.id;
      radio.value = value;
      if (saved === value) radio.checked = true;
      label.appendChild(radio);
      label.appendChild(el("span", null, labelText));
      panel.appendChild(label);
    });
  });
  const btn = el("button", "primary", "Save answers");
  btn.addEventListener("click", onQuestions);
  panel.appendChild(btn);
  if (st.preselected) {
    panel.appendChild(el("p", "muted",
      "This pre-selects: " + st.preselected.join(", ") + "."));
  }
  root.appendChild(panel);
}

async function onQuestions() {
  const answers = {};
  STATE.questions.forEach(function (q) {
    const picked = document.querySelector(
      'input[name="' + q.id + '"]:checked');
    answers[q.id] = picked ? picked.value : "";
  });
  setStatus("Saving answers.");
  const res = await api("POST", "/api/questions", { answers: answers });
  if (res.status !== 200 || !res.data) {
    setStatus("Save failed.");
    showError((res.data && res.data.error) || "request failed");
    return;
  }
  STATE = res.data.state;
  setStatus("Answers saved.");
  render();
}

function factText(fact) {
  if (fact && fact.unit === "bytes" &&
      typeof fact.value === "number") {
    return fmtBytes(fact.value);
  }
  const value = fact ? fact.value : fact;
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function screenPhone(root) {
  const st = STATE;
  if (!st.profile) {
    const panel = el("div", "panel");
    panel.appendChild(el("h2", null, "Your phone"));
    panel.appendChild(el("p", "muted",
      "Connect a phone first."));
    const btn = el("button", "primary", "Go to Connect");
    btn.addEventListener("click", function () { gotoStep("connect"); });
    panel.appendChild(btn);
    root.appendChild(panel);
    return;
  }
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "Your phone"));
  const tags = el("p", null);
  tags.appendChild(tag("SIMULATED"));
  tags.appendChild(tag(st.profile.name, "indigo"));
  panel.appendChild(tags);
  const wrap = el("div", "table-scroll");
  const table = document.createElement("table");
  table.className = "facts";
  const head = document.createElement("tr");
  ["Fact", "Value", "Status"].forEach(function (t) {
    head.appendChild(el("th", null, t));
  });
  table.appendChild(head);
  const facts = st.profile.data.facts || {};
  Object.keys(facts).sort().forEach(function (key) {
    const fact = facts[key];
    const row = document.createElement("tr");
    row.appendChild(el("td", null, key));
    row.appendChild(el("td", "value", factText(fact)));
    row.appendChild(el("td", null, fact.status || "unknown"));
    table.appendChild(row);
  });
  wrap.appendChild(table);
  panel.appendChild(wrap);
  root.appendChild(panel);
  root.appendChild(detailsBox("Profile details", [
    ["name", st.profile.name],
    ["sha256", st.profile.sha256]]));
}

function screenServices(root) {
  const st = STATE;
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "What it can do for you"));
  if (!st.profile) {
    panel.appendChild(el("p", "muted", "Connect a phone first."));
    root.appendChild(panel);
    return;
  }
  const checked = st.selection ? st.selection.services :
    (st.preselected || []);
  ["files", "assistant"].forEach(function (svc) {
    const label = el("label", "choice");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.name = "svc";
    box.value = svc;
    if (checked.indexOf(svc) >= 0) box.checked = true;
    label.appendChild(box);
    label.appendChild(el("span", null,
      svc === "files" ? "Files: keep files on the phone" :
      "Assistant: answer questions on the phone"));
    panel.appendChild(label);
  });
  const btn = el("button", "primary", "Check what fits");
  btn.addEventListener("click", onSelection);
  panel.appendChild(btn);
  root.appendChild(panel);

  const view = st.outputs.plan;
  if (view.status === "missing") return;
  if (view.status === "stale") {
    root.appendChild(staleBox(view, "services"));
    const det = outputDetails("Plan", view);
    if (det) root.appendChild(det);
    return;
  }
  renderPlan(root, view.data);
  const det = outputDetails("Plan", view);
  if (det) root.appendChild(det);
}

async function onSelection() {
  const boxes = document.querySelectorAll('input[name="svc"]:checked');
  const services = [];
  boxes.forEach(function (b) { services.push(b.value); });
  setStatus("Checking what fits.");
  const res = await api("POST", "/api/selection", { services: services });
  if (res.status !== 200 || !res.data) {
    setStatus("Check failed.");
    showError((res.data && res.data.error) || "request failed");
    return;
  }
  STATE = res.data.state;
  setStatus("Plan ready.");
  render();
}

function renderPlan(root, plan) {
  const panel = el("div", "panel");
  const result = plan.selection_result || "unknown";
  if (result === "refused") panel.classList.add("callout-refused");
  const tags = el("p", null);
  tags.appendChild(tag("result: " + result,
    result === "refused" ? "amber" : ""));
  tags.appendChild(tag("build: " + (plan.build_allowed || "none"),
    "indigo"));
  panel.appendChild(tags);
  (plan.fit.reasons || []).forEach(function (reason) {
    panel.appendChild(el("p", null, reason));
  });
  const wrap = el("div", "table-scroll");
  const table = document.createElement("table");
  table.className = "facts";
  const head = document.createElement("tr");
  ["Check", "Status", "Numbers"].forEach(function (t) {
    head.appendChild(el("th", null, t));
  });
  table.appendChild(head);
  (plan.fit.checks || []).forEach(function (check) {
    const row = document.createElement("tr");
    row.appendChild(el("td", null, check.name || ""));
    row.appendChild(el("td", null, check.status || ""));
    let numbers = "";
    if (typeof check.have_bytes === "number" &&
        typeof check.needed_bytes === "number") {
      numbers = "have " + fmtBytes(check.have_bytes) + ", need " +
        fmtBytes(check.needed_bytes);
    } else if (check.reason) {
      numbers = check.reason;
    }
    row.appendChild(el("td", "value", numbers));
    table.appendChild(row);
  });
  wrap.appendChild(table);
  panel.appendChild(wrap);
  const choice = plan.fit.model_choice || {};
  if (choice.status === "refused" || !choice.model_id) {
    panel.appendChild(el("p", null, "Model choice: refused."));
    (choice.reasons || []).forEach(function (reason) {
      panel.appendChild(el("p", "muted", reason));
    });
  } else {
    panel.appendChild(el("p", null,
      "Model choice: " + choice.model_id + "."));
  }
  panel.appendChild(el("p", "faint",
    "Provenance: " + (plan.provenance || "unknown") + "."));
  if (plan.notes) {
    panel.appendChild(el("p", "muted", plan.notes));
    panel.appendChild(el("p", "faint", "model explanation, not checked."));
  }
  root.appendChild(panel);

  const strat = el("div", "panel");
  strat.appendChild(el("h2", null, "Approaches"));
  (plan.strategies || []).forEach(function (s) {
    const line = el("p", null,
      (s.id || "") + ": " + (s.status || ""));
    strat.appendChild(line);
    (s.missing || []).forEach(function (m) {
      strat.appendChild(el("p", "faint", "missing: " + m));
    });
  });
  root.appendChild(strat);
}

function screenResearch(root) {
  const st = STATE;
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "Researching how to do it safely"));
  panel.appendChild(el("p", "muted", st.outputs.research.message));
  const live = el("button", "ghost", "Go live");
  live.disabled = true;
  live.title = st.live.message;
  panel.appendChild(live);
  panel.appendChild(el("p", "faint", st.live.message + "."));
  root.appendChild(panel);
  const m5 = el("div", "panel");
  m5.appendChild(el("h2", null, "Install plan"));
  const mv = st.outputs.m5;
  if (mv.status === "saved" && mv.data) {
    renderM5Saved(m5, mv);
  } else if (mv.status === "outdated") {
    m5.appendChild(el("p", "muted", mv.message + "."));
    m5.appendChild(m5InputDetails(mv));
  } else {
    m5.appendChild(el("p", "muted", mv.message + "."));
  }
  root.appendChild(m5);
}

function m5DetailValue(value) {
  if (value === null || value === undefined) return "none";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function m5InputDetails(mv) {
  const rows = [["file sha256", mv.sha256 || "none"]];
  const inputs = mv.inputs || {};
  ["profile", "plan", "selection"].forEach(function (key) {
    const pair = inputs[key] || {};
    rows.push(["recorded " + key, m5DetailValue(pair.recorded)]);
    rows.push(["current " + key, m5DetailValue(pair.current)]);
  });
  return detailsBox("Install plan details", rows);
}

function renderM5Saved(m5, mv) {
  const data = mv.data;
  m5.appendChild(el("p", null, mv.message + "."));
  const tags = el("p", null);
  tags.appendChild(tag("SIMULATED"));
  m5.appendChild(tags);
  const result = data.result === "do_not_install" ? "Do not install" :
    String(data.result);
  m5.appendChild(el("p", null, "Result: " + result + "."));
  (data.pages || []).forEach(function (page) {
    m5.appendChild(el("p", null,
      (page.id || "page") + ": " + page.url + " (page " +
      page.byte_size + " bytes; excerpt ranges " +
      JSON.stringify(page.excerpt_ranges) + ")."));
  });
  (data.drafts || []).forEach(function (draft) {
    m5.appendChild(el("p", null,
      "Draft " + draft.index + ": " + draft.verdict + "."));
    (draft.rules || []).forEach(function (rule) {
      m5.appendChild(el("p", "muted",
        rule.rule + ": " + rule.status + "."));
      (rule.reasons || []).forEach(function (reason) {
        m5.appendChild(el("p", "faint", reason));
      });
    });
    const usage = draft.usage || {};
    m5.appendChild(el("p", "faint",
      "Tokens: prompt " + usage.prompt_tokens + ", completion " +
      usage.completion_tokens + ", reasoning " +
      usage.reasoning_tokens + "."));
  });
  if (data.refusal) {
    m5.appendChild(el("p", null,
      "Refusal: " + data.refusal.reason));
    m5.appendChild(el("p", "muted", "Quote:"));
    m5.appendChild(el("p", "mono", data.refusal.quote));
    m5.appendChild(el("p", "muted",
      "Source: " + data.refusal.url + " (" + data.refusal.rule +
      ", " + data.refusal.fetch_id + ")."));
  }
  const model = data.model || {};
  m5.appendChild(el("p", null,
    "Model: " + model.id + " (" + model.provider + ")."));
  m5.appendChild(el("p", "faint",
    "Install-routing proposal: not a validated install procedure."));
  m5.appendChild(m5InputDetails(mv));
}

function screenBuild(root) {
  const st = STATE;
  const planView = st.outputs.plan;
  const buildView = st.outputs.build;
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "Building your phone's new system"));
  if (planView.status !== "current") {
    panel.appendChild(el("p", "muted",
      planView.status === "missing" ?
      "Confirm services first." :
      "The plan is out of date: re-run Services."));
    const btn = el("button", "primary", "Go to Services");
    btn.addEventListener("click", function () { gotoStep("services"); });
    panel.appendChild(btn);
    root.appendChild(panel);
    return;
  }
  const plan = planView.data;
  if (plan.selection_result === "refused") {
    panel.appendChild(warnBox("Build blocked: the plan refuses this " +
      "phone. " + (plan.fit.reasons || []).join(" ")));
    root.appendChild(panel);
    return;
  }
  if (plan.build_allowed !== "emulator_only") {
    panel.appendChild(warnBox("Build blocked: the plan does not allow " +
      "a build."));
    root.appendChild(panel);
    return;
  }
  panel.appendChild(el("p", "muted",
    "Fresh builds only: every build is made new for this plan."));
  const btn = el("button", "primary", "Build");
  btn.addEventListener("click", onBuild);
  panel.appendChild(btn);
  root.appendChild(panel);

  if (buildView.status === "stale") {
    root.appendChild(staleBox(buildView, "build"));
    return;
  }
  const jobs = Object.keys(st.jobs).map(function (k) { return st.jobs[k]; })
    .filter(function (j) { return j.step === "build"; });
  if (jobs.length === 0 && buildView.status === "missing") return;
  renderBuildStatus(root);
}

function latestBuildJob() {
  const ids = Object.keys(STATE.jobs).filter(function (id) {
    return STATE.jobs[id].step === "build";
  }).sort();
  if (ids.length === 0) return null;
  return { id: ids[ids.length - 1], job: STATE.jobs[ids[ids.length - 1]] };
}

async function onBuild() {
  setStatus("Starting the build.");
  const res = await api("POST", "/api/build", {});
  if (res.status !== 202 || !res.data) {
    setStatus("Build refused.");
    showError((res.data && res.data.error) || "request failed");
    return;
  }
  STATE = res.data.state;
  setStatus("Build running: " + res.data.build_id + ".");
  render();
  startBuildPoll();
}

function startBuildPoll() {
  if (POLL) clearInterval(POLL);
  document.getElementById("screen").classList.add("loading");
  POLL = setInterval(async function () {
    const found = latestBuildJob();
    if (!found || found.job.status !== "running") {
      clearInterval(POLL);
      POLL = null;
      document.getElementById("screen").classList.remove("loading");
      const res = await api("GET", "/api/state", null);
      if (res.status === 200 && res.data) {
        CSRF = res.data.csrf;
        STATE = res.data.state;
        render();
      }
      return;
    }
    const res = await api("GET", "/api/jobs/" + found.id, null);
    if (res.status === 200 && res.data) {
      paintProgress(res.data.progress || []);
      if (res.data.job.status !== "running") {
        const st = await api("GET", "/api/state", null);
        if (st.status === 200 && st.data) {
          CSRF = st.data.csrf;
          STATE = st.data.state;
          setStatus("Build " + res.data.job.status + ".");
          render();
        }
        clearInterval(POLL);
        POLL = null;
        document.getElementById("screen").classList.remove("loading");
      }
    }
  }, 2000);
}

function renderBuildStatus(root) {
  const found = latestBuildJob();
  const panel = el("div", "panel");
  if (!found) return;
  panel.appendChild(el("p", null,
    "Job " + found.id + ": " + found.job.status + "."));
  if (found.job.status === "running") {
    const bar = el("div", "progress");
    bar.appendChild(el("span", null));
    panel.appendChild(bar);
    const working = el("p", "muted");
    working.appendChild(el("span", "dot", "\u25cf "));
    working.appendChild(el("span", null,
      "Working. This takes a few minutes."));
    panel.appendChild(working);
  }
  if (found.job.reason) {
    panel.appendChild(el("p", "muted", found.job.reason));
  }
  const log = el("pre", "log", "");
  log.id = "build-log";
  panel.appendChild(log);
  root.appendChild(panel);
  if (found.job.status === "running") {
    api("GET", "/api/jobs/" + found.id, null).then(function (res) {
      if (res.status === 200 && res.data) {
        paintProgress(res.data.progress || []);
      }
    });
    startBuildPoll();
  }
  const view = STATE.outputs.build;
  if (view.status === "current" && view.data) {
    renderBuildResult(root, view.data);
    const det = outputDetails("Build", view);
    if (det) root.appendChild(det);
  }
}

function paintProgress(lines) {
  const log = document.getElementById("build-log");
  if (!log) return;
  log.textContent = lines.join("\n");
}

function renderBuildResult(root, data) {
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "Build done"));
  const summary = data.summary || {};
  panel.appendChild(el("p", null, "Build " + (summary.build_id || "") +
    " for " + (summary.selection || []).join(", ") + "."));
  if (summary.image && summary.partition_limit_bytes) {
    panel.appendChild(el("p", null,
      "Image " + summary.image.path + ": " +
      fmtBytes(summary.image.bytes) + " (limit " +
      fmtBytes(summary.partition_limit_bytes) + ")."));
  }
  if (summary.gz) {
    panel.appendChild(el("p", null,
      "Transfer " + summary.gz.path + ": " +
      fmtBytes(summary.gz.bytes) + "."));
  }
  (data.checks || []).forEach(function (check) {
    panel.appendChild(el("p", "muted",
      "Check passed: " + check.check + " (" +
      (check.services || []).join(", ") + ")."));
  });
  root.appendChild(panel);
}

async function screenBoot(root) {
  const st = STATE;
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "Boot it on your Mac"));
  panel.appendChild(el("p", null, "Target: " + st.boot_target + "."));
  const view = st.outputs.build;
  if (view.status !== "current") {
    panel.appendChild(el("p", "muted",
      view.status === "missing" ?
      "Build first." : "The build is out of date: re-run Build."));
    root.appendChild(panel);
    return;
  }
  const key = view.output_sha256;
  if (BOOT_CACHE.key !== key) {
    panel.appendChild(el("p", "muted", "Loading the steps."));
    root.appendChild(panel);
    document.getElementById("screen").classList.add("loading");
    const res = await api("GET", "/api/boot", null);
    document.getElementById("screen").classList.remove("loading");
    if (res.status !== 200 || !res.data) {
      setStatus("Boot steps failed.");
      showError((res.data && res.data.error) || "request failed");
      return;
    }
    BOOT_CACHE = { key: key, text: res.data.steps };
    render();
    return;
  }
  const row = el("div", "row");
  const copy = el("button", "small", "Copy the steps");
  copy.addEventListener("click", function () {
    copyText(BOOT_CACHE.text);
  });
  row.appendChild(copy);
  panel.appendChild(row);
  panel.appendChild(el("pre", "steps", BOOT_CACHE.text));
  root.appendChild(panel);
  const phone = el("div", "panel");
  phone.appendChild(el("h2", null, "On a real phone"));
  phone.appendChild(el("p", "muted", st.outputs.m5.message + "."));
  root.appendChild(phone);
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () {
      setStatus("Steps copied.");
    }, function () {
      setStatus("Copy failed: select the text by hand.");
    });
  } else {
    setStatus("Copy is not available: select the text by hand.");
  }
}

function screenDone(root) {
  const st = STATE;
  const panel = el("div", "panel");
  panel.appendChild(el("h2", null, "Done"));
  const view = st.outputs.build;
  if (view.status === "current" && view.data) {
    const summary = view.data.summary || {};
    panel.appendChild(el("p", null,
      "Built " + (summary.build_id || "") + " for " +
      (summary.selection || []).join(", ") + "."));
    if (summary.image) {
      panel.appendChild(el("p", "muted",
        summary.image.path + ": " + fmtBytes(summary.image.bytes) + "."));
    }
  } else {
    panel.appendChild(el("p", "muted",
      "Nothing built yet. Work through the steps above."));
  }
  root.appendChild(panel);
  const link = el("div", "panel");
  link.appendChild(el("h2", null, "Your phone's page"));
  link.appendChild(el("p", "muted",
    "The phone's page link " + st.phone_link.message + "."));
  root.appendChild(link);
}

function showError(text) {
  const screen = elClear("screen");
  screen.appendChild(warnBox(text));
  const btn = el("button", "ghost", "Back");
  btn.addEventListener("click", render);
  screen.appendChild(btn);
}

function render() {
  const buttons = document.querySelectorAll("#rail-list button");
  const at = STEPS.indexOf(STEP);
  buttons.forEach(function (b) {
    if (b.getAttribute("data-step") === STEP) {
      b.setAttribute("aria-current", "step");
    } else {
      b.removeAttribute("aria-current");
    }
    b.classList.toggle("done",
      STEPS.indexOf(b.getAttribute("data-step")) >= 0 &&
      STEPS.indexOf(b.getAttribute("data-step")) < at);
  });
  const root = elClear("screen");
  if (!STATE) {
    root.appendChild(el("p", "muted", "Loading."));
    return;
  }
  if (STEP === "welcome") screenWelcome(root);
  else if (STEP === "connect") screenConnect(root);
  else if (STEP === "questions") screenQuestions(root);
  else if (STEP === "phone") screenPhone(root);
  else if (STEP === "services") screenServices(root);
  else if (STEP === "research") screenResearch(root);
  else if (STEP === "build") screenBuild(root);
  else if (STEP === "boot") screenBoot(root);
  else if (STEP === "done") screenDone(root);
}

async function signOut() {
  await api("POST", "/api/signout", {});
  CSRF = "";
  STATE = null;
  if (POLL) { clearInterval(POLL); POLL = null; }
  const root = elClear("screen");
  root.appendChild(el("p", "muted",
    "Signed out. The start link works once; restart the server for " +
    "a new link."));
  setStatus("Signed out.");
}

async function boot() {
  const buttons = document.querySelectorAll("#rail-list button");
  buttons.forEach(function (b) {
    b.addEventListener("click", function () {
      gotoStep(b.getAttribute("data-step"));
    });
  });
  document.getElementById("signout").addEventListener("click", signOut);
  try {
    const res = await api("GET", "/api/state", null);
    if (res.status !== 200 || !res.data) {
      showError("Could not load. Sign in again from the terminal link.");
      return;
    }
    CSRF = res.data.csrf;
    STATE = res.data.state;
    setStatus("Ready.");
    render();
  } catch (e) {
    showError("Could not reach the server.");
  }
}

document.addEventListener("DOMContentLoaded", boot);
