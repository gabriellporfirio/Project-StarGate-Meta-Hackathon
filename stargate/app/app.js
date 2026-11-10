/* Guided phone setup client. Plain fetch + DOM. Dynamic text (model
   text, quotes, file names) is inserted with textContent only, never
   as HTML. Styles come from app.css classes; sizes that depend on
   data are set through the style object, never a style attribute. */
"use strict";

const STEPS = ["welcome", "connect", "questions", "phone", "services",
  "research", "build", "boot", "done"];
const SVG_NS = "http://www.w3.org/2000/svg";

const PHONE_LABELS = {
  flagship: ["Strong recent phone", "12 GB memory, unlockable"],
  budget: ["Budget phone", "2 GB memory, cannot be unlocked"],
  old32: ["Old 32-bit phone", "1 GB memory, Android 8"],
  locked: ["Locked flagship", "8 GB memory, unlock not confirmed"],
  relic: ["Very old phone", "256 MB memory, Android 4.4"]
};

const ROUTE_TITLES = {
  image: "Full clean system",
  image_or_takeover: "Clean system, or takeover",
  takeover: "Takeover",
  none: "No install route"
};

const UNLOCK_TEXT = {
  already_unlocked: "Already unlocked",
  allowed_now: "Can be unlocked",
  not_allowed_now: "Unlocking is switched off",
  no_official_path: "Cannot be unlocked",
  unknown: "Not confirmed yet"
};

let CSRF = "";
let STATE = null;
let STEP = "welcome";
let POLL = null;
let BOOT_CACHE = { key: "", text: "" };
let PICKED = null;      /* services ticked on screen, before saving */
let DRAFT = {};         /* answers picked on screen, before saving */
let SHOW_RAW = false;
let DEVICES = null;     /* { devices: [], error: "" } after a look */

/* -- small helpers ------------------------------------------------ */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function add(parent) {
  for (let i = 1; i < arguments.length; i++) {
    if (arguments[i]) parent.appendChild(arguments[i]);
  }
  return parent;
}

function svg(tag, attrs, kids) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.keys(attrs || {}).forEach(function (k) {
    node.setAttribute(k, attrs[k]);
  });
  (kids || []).forEach(function (kid) { node.appendChild(kid); });
  return node;
}

function button(cls, text, onClick) {
  const node = el("button", cls, text);
  node.type = "button";
  if (onClick) node.addEventListener("click", onClick);
  return node;
}

function fmtSize(n) {
  if (typeof n !== "number") return "unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n, u = 0;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  const digits = (v >= 10 || u === 0 || Math.abs(v - Math.round(v)) < 0.05) ? 0 : 1;
  return v.toFixed(digits) + " " + units[u];
}

function fmtBytes(n) {
  if (typeof n !== "number") return "unknown";
  return fmtSize(n) + " (" + n + " bytes)";
}

function track(percent, kind, thick) {
  const outer = el("span", thick ? "track thick" : "track");
  const inner = el("span", kind || "");
  outer.appendChild(inner);
  const width = Math.max(0, Math.min(100, percent)) + "%";
  window.requestAnimationFrame(function () {
    window.requestAnimationFrame(function () { inner.style.width = width; });
  });
  return outer;
}

function note(kind, icon, bold, text) {
  const box = el("div", "note " + (kind || ""));
  add(box, el("span", "ico", icon));
  const body = el("span");
  if (bold) add(body, el("b", null, bold + " "));
  add(body, document.createTextNode(text || ""));
  return add(box, body);
}

function heading(title, sub) {
  const box = el("div", "stack up");
  add(box, el("h2", null, title));
  if (sub) add(box, el("p", "sub", sub));
  return box;
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
  document.getElementById("status-line").textContent = text;
}

function elClear(id) {
  const node = document.getElementById(id);
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

function detailsBox(title, rows) {
  const box = el("details", "panel tight");
  add(box, el("summary", null, title));
  rows.forEach(function (row) {
    add(box, el("p", null, row[0] + ": " + row[1]));
  });
  return box;
}

function outputDetails(name, view) {
  if (!view || view.output_sha256 === null ||
      view.output_sha256 === undefined) return null;
  const rows = [["output", view.output_sha256 || "none"]];
  const inputs = view.input_hashes || {};
  Object.keys(inputs).sort().forEach(function (k) {
    rows.push(["input " + k, inputs[k] || "none"]);
  });
  rows.push(["job", view.job_id || "none"]);
  return detailsBox(name + " details", rows);
}

function wantsDetails() {
  return !!(STATE && STATE.answers && STATE.answers.data &&
    STATE.answers.data.q2 === "yes");
}

function fact(id) {
  const facts = (STATE.profile && STATE.profile.data.facts) || {};
  const item = facts[id];
  if (!item || item.status === "unknown") return null;
  return item.value;
}

function phoneName() {
  const maker = fact("identity.manufacturer");
  const model = fact("identity.model");
  if (model && maker && model.indexOf(maker) !== 0) return maker + " " + model;
  return model || maker || "your phone";
}

function gotoStep(step) {
  if (POLL) { clearInterval(POLL); POLL = null; }
  document.getElementById("screen").classList.remove("loading");
  STEP = step;
  render();
  document.getElementById("screen").focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function fail(res, fallback) {
  setStatus((res.data && res.data.error) || fallback || "Request failed.");
}

/* -- welcome ------------------------------------------------------ */

function orbitArt() {
  const grad = svg("linearGradient", { id: "g1", x1: "0", x2: "1", y1: "0", y2: "1" }, [
    svg("stop", { offset: "0", "stop-color": "#5EEAD4" }),
    svg("stop", { offset: "1", "stop-color": "#818CF8" })]);
  const dot1 = svg("circle", { cx: "330", cy: "190", r: "4", fill: "#5EEAD4" }, [
    svg("animateTransform", { attributeName: "transform", type: "rotate",
      from: "0 190 190", to: "360 190 190", dur: "9s", repeatCount: "indefinite" })]);
  const dot2 = svg("circle", { cx: "50", cy: "190", r: "3", fill: "#818CF8" }, [
    svg("animateTransform", { attributeName: "transform", type: "rotate",
      from: "0 190 190", to: "-360 190 190", dur: "14s", repeatCount: "indefinite" })]);
  const phone = svg("g", { "class": "float" }, [
    svg("rect", { x: "150", y: "110", width: "80", height: "160", rx: "14", fill: "#0B1020", stroke: "url(#g1)", "stroke-width": "2" }),
    svg("rect", { x: "160", y: "128", width: "60", height: "112", rx: "4", fill: "#0F1628" }),
    svg("rect", { x: "166", y: "138", width: "22", height: "22", rx: "5", fill: "#5EEAD4", opacity: ".85" }),
    svg("rect", { x: "192", y: "138", width: "22", height: "22", rx: "5", fill: "#818CF8", opacity: ".85" }),
    svg("rect", { x: "166", y: "164", width: "22", height: "22", rx: "5", fill: "#2A3550" }),
    svg("rect", { x: "192", y: "164", width: "22", height: "22", rx: "5", fill: "#2A3550" }),
    svg("rect", { x: "166", y: "198", width: "48", height: "4", rx: "2", fill: "#2A3550" }),
    svg("rect", { x: "166", y: "208", width: "34", height: "4", rx: "2", fill: "#2A3550" }),
    svg("circle", { cx: "190", cy: "255", r: "4", fill: "#2A3550" })]);
  return svg("svg", { width: "380", height: "380", viewBox: "0 0 380 380", "aria-hidden": "true" }, [
    svg("defs", {}, [grad]),
    svg("circle", { "class": "spin", cx: "190", cy: "190", r: "170", fill: "none", stroke: "#1E2638", "stroke-width": "1", "stroke-dasharray": "2 10" }),
    svg("circle", { "class": "spinr", cx: "190", cy: "190", r: "140", fill: "none", stroke: "url(#g1)", "stroke-width": "1.5", "stroke-dasharray": "60 30 8 30", opacity: ".7" }),
    svg("circle", { "class": "spin", cx: "190", cy: "190", r: "112", fill: "none", stroke: "#2A3550", "stroke-width": "1", "stroke-dasharray": "1 6" }),
    phone, dot1, dot2]);
}

function screenWelcome(root) {
  const split = el("section", "split");
  const col = el("div", "col");
  add(col, el("span", "kicker up", "OLD PHONE · NEW JOB"));
  add(col, el("h1", "up d1", "Turn the phone in your drawer into something useful."));
  add(col, el("p", "lead up d2",
    "Plug it in. We check what it can do, suggest what it could run for " +
    "you, research how to do it safely, then build and install a clean " +
    "system made just for it."));
  const pills = el("div", "row up d3");
  ["1 · Check", "2 · Suggest", "3 · Research",
    "4 · Build and install"].forEach(function (t) {
    add(pills, el("span", "pill", t));
  });
  add(col, pills);
  add(col, el("p", "faint up d4",
    "About 20 minutes. Nothing changes on your phone until you say so."));
  const art = el("div", "art fade d2");
  add(art, orbitArt());
  add(root, add(split, col, art));
}

/* -- connect ------------------------------------------------------ */

function cableArt(found, looking) {
  const stroke = found ? "#5EEAD4" : "#2A3550";
  const kids = [
    svg("rect", { x: "20", y: "70", width: "110", height: "74", rx: "8", fill: "#0F1628", stroke: "#2A3550", "stroke-width": "1.5" }),
    svg("rect", { x: "8", y: "144", width: "134", height: "10", rx: "3", fill: "#1E2638" }),
    svg("path", { "class": "dash", d: "M142 120 C 190 120, 190 150, 214 150", fill: "none", stroke: stroke, "stroke-width": "2.5", "stroke-dasharray": "6 6" }),
    svg("rect", { x: "214", y: "60", width: "66", height: "132", rx: "12", fill: "#0B1020", stroke: stroke, "stroke-width": "2" }),
    svg("rect", { x: "222", y: "74", width: "50", height: "96", rx: "3", fill: "#0F1628" })];
  if (looking) {
    kids.push(svg("circle", { "class": "ring", cx: "247", cy: "126", r: "22", fill: "none", stroke: "#5EEAD4", "stroke-width": "1.5" }));
  }
  if (found) {
    kids.push(svg("circle", { cx: "247", cy: "122", r: "16", fill: "#13283A" }));
    kids.push(svg("path", { d: "M239 122l6 6 11-12", fill: "none", stroke: "#5EEAD4", "stroke-width": "3", "stroke-linecap": "round", "stroke-linejoin": "round" }));
  }
  return svg("svg", { width: "300", height: "260", viewBox: "0 0 300 260", "aria-hidden": "true" }, kids);
}

function screenConnect(root, looking) {
  const st = STATE;
  const split = el("section", "split");
  const col = el("div", "col");
  add(col, el("h2", "up", "Plug your phone in"));
  const list = el("ol", "numbered");
  [["Use a data cable.", "Some cables only charge. If nothing happens, try another."],
   ["Turn on USB debugging.", "Settings, About phone, tap Build number 7 times, then Developer options."],
   ["Unlock the screen and tap Allow.", "Your phone asks if it can trust this computer."]
  ].forEach(function (row, i) {
    const li = el("li", "up d" + (i + 1));
    add(li, el("span", "n", String(i + 1)));
    const body = el("span");
    add(body, el("b", null, row[0] + " "), el("span", "d", row[1]));
    add(list, add(li, body));
  });
  add(col, list);
  add(col, note("warn up d4", "⚠", null,
    "Use a phone you don't need any more. Installing later will erase " +
    "it, and we'll ask you clearly before that happens. Back up anything " +
    "you want to keep now."));

  const real = el("div", "panel up d5");
  const realHead = el("div", "between");
  realHead.style.alignItems = "center";
  add(realHead, el("h3", null, "Phones on the cable"),
    button("ghost", DEVICES ? "Look again" : "Look for phones", onLook));
  add(real, realHead);
  if (!DEVICES) {
    add(real, el("p", "muted",
      "We only read facts from the phone. Nothing on it is changed."));
  } else if (DEVICES.error) {
    add(real, note("warn", "\u26a0", null, "Could not look: " +
      DEVICES.error + "."));
  } else if (DEVICES.devices.length === 0) {
    add(real, el("p", "muted",
      "No phone found. Check the cable and the three steps above."));
  } else {
    DEVICES.devices.forEach(function (device) {
      const row = el("div", "between");
      row.style.alignItems = "center";
      const name = el("div", "stack");
      add(name, el("span", "label", device.model || "Unnamed phone"),
        el("span", "faint", device.ready ? "Ready, id ends " + device.hint :
          device.state === "unauthorized" ?
          "Waiting: unlock the phone and tap Allow" :
          "Not ready (" + device.state + ")"));
      const go = button("ghost", "Check this phone", function () {
        onConnectDevice(device.id);
      });
      go.disabled = !device.ready;
      add(real, add(row, name, go));
    });
  }
  add(col, real);

  const pick = el("div", "panel up d6");
  const head = el("div", "between");
  add(head, el("h3", null, "Or try an example phone"),
    el("span", "chip sim", "SIMULATED"));
  add(pick, head);
  add(pick, el("p", "muted",
    "No phone to hand? These example phones go through the same checks."));
  const grid = el("div", "grid choices");
  Object.keys(PHONE_LABELS).filter(function (name) {
    return !st.fixtures || st.fixtures.indexOf(name) >= 0;
  }).forEach(function (name) {
    const label = PHONE_LABELS[name] || [name, ""];
    const b = button("choice card", null, function () { onConnect(name); });
    b.setAttribute("aria-pressed",
      st.profile && st.profile.name === name ? "true" : "false");
    add(b, el("span", "t", label[0]), el("span", "s", label[1]));
    add(grid, b);
  });
  add(pick, grid);
  add(col, pick);

  const art = el("div", "art");
  const found = !!st.profile && !looking;
  add(art, cableArt(found, !!looking));
  if (looking) {
    add(art, el("span", "pulse muted", "Looking at the phone…"));
  } else if (found) {
    const box = el("div", "stack up");
    box.style.alignItems = "center";
    add(box, el("h3", null, "Found: " + phoneName()));
    const release = fact("identity.android_release");
    add(box, el("span", "mono muted",
      (release ? "Android " + release + " · " : "") + "checked, nothing changed"));
    add(art, box);
  } else {
    add(art, el("span", "muted", "Waiting for a phone…"));
  }
  add(root, add(split, col, art));
}

async function onLook() {
  setStatus("Looking for phones.");
  const res = await api("GET", "/api/devices", null);
  if (res.status !== 200 || !res.data) { fail(res, "Could not look."); return; }
  DEVICES = res.data;
  setStatus(DEVICES.error ? "Could not look for phones." :
    DEVICES.devices.length + " phone(s) found.");
  render();
}

async function onConnectDevice(id) {
  setStatus("Reading the phone. Nothing on it is changed.");
  const root = elClear("screen");
  screenConnect(root, true);
  const res = await api("POST", "/api/connect", { device: id });
  if (res.status !== 200 || !res.data) {
    fail(res, "Check failed.");
    render();
    return;
  }
  STATE = res.data.state;
  PICKED = null;
  setStatus("Phone checked. Nothing on it was changed.");
  render();
}

async function onConnect(name) {
  setStatus("Checking the phone.");
  const root = elClear("screen");
  screenConnect(root, true);
  const res = await api("POST", "/api/connect", { profile: name });
  if (res.status !== 200 || !res.data) {
    fail(res, "Check failed.");
    render();
    return;
  }
  STATE = res.data.state;
  PICKED = null;
  setStatus("Phone checked. Nothing on it was changed.");
  render();
}

/* -- questions ---------------------------------------------------- */

const QUESTION_HELP = {
  files: "Photos and documents, kept at home.",
  assistant: "Ask things in plain words.",
  both: "Files and an assistant together.",
  yes: "Show the numbers behind each screen.",
  no: "Keep it simple."
};

function screenQuestions(root) {
  const st = STATE;
  const saved = (st.answers && st.answers.data) || {};
  const box = el("section", "col wide");
  add(box, heading("A few quick questions",
    "So we suggest things you'll actually use."));
  st.questions.forEach(function (q, qi) {
    const group = el("div", "stack up d" + (qi + 1));
    group.style.gap = "10px";
    add(group, el("span", "label", q.text));
    const grid = el("div", "grid choices");
    q.options.forEach(function (opt) {
      const value = opt[0];
      const current = DRAFT[q.id] !== undefined ? DRAFT[q.id] : saved[q.id];
      const b = button("choice card", null, function () {
        DRAFT[q.id] = value;
        saveAnswers();
      });
      b.setAttribute("aria-pressed", current === value ? "true" : "false");
      add(b, el("span", "t", opt[1]),
        el("span", "s", QUESTION_HELP[value] || ""));
      add(grid, b);
    });
    add(group, grid);
    add(box, group);
  });
  add(root, box);
}

async function saveAnswers() {
  const saved = (STATE.answers && STATE.answers.data) || {};
  const answers = {};
  let complete = true;
  STATE.questions.forEach(function (q) {
    const value = DRAFT[q.id] !== undefined ? DRAFT[q.id] : saved[q.id];
    if (value === undefined) complete = false;
    answers[q.id] = value;
  });
  if (!complete) { render(); return; }
  const res = await api("POST", "/api/questions", { answers: answers });
  if (res.status !== 200 || !res.data) { fail(res, "Save failed."); return; }
  STATE = res.data.state;
  DRAFT = {};
  PICKED = null;
  setStatus("Answers saved.");
  render();
}

/* -- your phone --------------------------------------------------- */

function factCard(name, value, noteText, percent, tagText, tagKind, i) {
  const card = el("div", "panel fact up d" + Math.min(i, 6));
  const head = el("div", "between");
  add(head, el("span", "k", name));
  if (tagText) add(head, el("span", "chip " + (tagKind || ""), tagText));
  add(card, head);
  add(card, el("span", String(value).length > 14 ? "v small" : "v", value));
  if (percent !== null) add(card, track(percent, tagKind === "amber" ? "amber" : ""));
  add(card, el("span", "muted", noteText));
  return card;
}

function needConnect(root, title) {
  const box = el("section", "col");
  add(box, heading(title, "Connect a phone first."));
  add(box, button("ghost", "Go to Connect", function () { gotoStep("connect"); }));
  add(root, box);
}

function verdictText(offers) {
  if (!offers) return ["Checked", ""];
  if (offers.verdict !== "usable") return ["Can't be used", "amber"];
  const ids = {};
  offers.services.forEach(function (s) { ids[s.id] = s; });
  if (ids.assistant && ids.assistant.mode === "on_phone") {
    return ["Enough for files and an AI model", ""];
  }
  return ["Good for files and light services", ""];
}

function screenPhone(root) {
  const st = STATE;
  if (!st.profile) { needConnect(root, "Here's your phone"); return; }
  const offers = st.offers;
  const top = el("div", "between up");
  top.style.alignItems = "flex-end";
  top.style.flexWrap = "wrap";
  add(top, heading("Here's your phone",
    "We read this from the phone. We didn't change anything."));
  const verdict = verdictText(offers);
  add(top, el("span", "chip big " + verdict[1], verdict[0]));
  add(root, top);

  const ram = fact("resources.ram_total");
  const free = fact("resources.data_free");
  const abis = fact("resources.cpu_abis") || [];
  const cores = fact("resources.cpu_cores");
  const release = fact("identity.android_release");
  const is64 = abis.indexOf("arm64-v8a") >= 0 || abis.indexOf("x86_64") >= 0;
  const gib = 1073741824;
  const grid = el("div", "grid");
  add(grid, factCard("MEMORY", ram === null ? "unknown" : fmtSize(ram),
    ram === null ? "Could not be read." :
    ram >= 4 * gib ? "Plenty. Room for an AI model on the phone." :
    ram >= 1.5 * gib ? "Enough for every light service and a small AI model." :
    ram >= 0.75 * gib ? "Enough for light services." : "Too little to run services.",
    ram === null ? null : 100 * ram / (8 * gib),
    ram === null ? "unknown" : ram >= 0.75 * gib ? "good" : "low",
    ram !== null && ram >= 0.75 * gib ? "" : "amber", 1));
  add(grid, factCard("FREE SPACE", free === null ? "unknown" : fmtSize(free),
    free === null ? "Could not be read." :
    free >= 8 * gib ? "Lots of room for your files." :
    free >= 0.5 * gib ? "Room for the services and some files." : "Very little room.",
    free === null ? null : 100 * free / (64 * gib),
    free === null ? "unknown" : free >= 0.5 * gib ? "good" : "low",
    free !== null && free >= 0.5 * gib ? "" : "amber", 2));
  add(grid, factCard("PROCESSOR", (is64 ? "64-bit" : "32-bit") +
    (cores ? " · " + cores + " cores" : ""),
    is64 ? "Can run AI models on the phone." :
    "Too old to run AI models on the phone itself.",
    null, is64 ? "good" : "older", is64 ? "" : "amber", 3));
  add(grid, factCard("ANDROID", release ? "Android " + release : "unknown",
    "What the phone runs today. The new system replaces or takes over this.",
    null, null, "", 4));
  if (offers) {
    const unlock = UNLOCK_TEXT[offers.unlock_state] || "Not confirmed yet";
    add(grid, factCard("BOOTLOADER", unlock,
      "Decides whether the phone's system can be replaced.",
      null, offers.route === "image" ? "open" : offers.route === "none" ? "blocked" : "locked",
      offers.route === "image" ? "" : "amber", 5));
    add(grid, factCard("HOW WE'LL INSTALL", ROUTE_TITLES[offers.route] || "unknown",
      offers.route_text, null, null, "", 6));
  }
  add(root, grid);

  if (offers && offers.verdict !== "usable") {
    const stop = el("div", "note stop up");
    add(stop, el("span", "ico", "⊘"));
    const body = el("div", "stack");
    add(body, el("b", null, "This phone can't run anything useful."));
    (offers.reasons || []).forEach(function (reason) {
      add(body, el("span", null, reason));
    });
    add(body, el("span", null, "Nothing was changed on it. Try another phone."));
    add(root, add(stop, body));
  }

  if (wantsDetails() || SHOW_RAW) {
    const panel = el("div", "panel");
    const head = el("div", "between");
    add(head, el("h3", null, "Every fact we read"));
    if (st.profile.data.simulated) {
      add(head, el("span", "chip sim", "SIMULATED · " + st.profile.name));
    }
    add(panel, head);
    const wrap = el("div", "table-scroll");
    const table = el("table", "facts");
    const hr = el("tr");
    ["Fact", "Value", "Status"].forEach(function (t) { add(hr, el("th", null, t)); });
    add(table, hr);
    const facts = st.profile.data.facts || {};
    Object.keys(facts).sort().forEach(function (key) {
      const item = facts[key];
      const row = el("tr");
      let text = item.unit === "bytes" && typeof item.value === "number" ?
        fmtBytes(item.value) : (typeof item.value === "string" ? item.value :
        JSON.stringify(item.value));
      add(row, el("td", null, key), el("td", "value", text),
        el("td", null, item.status || "unknown"));
      add(table, row);
    });
    add(panel, add(wrap, table));
    add(root, panel);
    add(root, detailsBox("Profile details", [["name", st.profile.name],
      ["sha256", st.profile.sha256]]));
  } else {
    add(root, button("quiet", "Show every fact we read", function () {
      SHOW_RAW = true;
      render();
    }));
  }
}

/* -- services ----------------------------------------------------- */

function currentPick() {
  const st = STATE;
  if (PICKED === null) {
    const base = st.selection ? st.selection.services :
      (st.preselected || (st.offers ? st.offers.selectable : []));
    PICKED = base.slice();
  }
  return PICKED;
}

function offerCard(offer, i) {
  const selectable = STATE.offers.selectable.indexOf(offer.id) >= 0;
  const picked = currentPick().indexOf(offer.id) >= 0;
  const card = button("offer up d" + Math.min(i + 1, 6), null, function () {
    const at = PICKED.indexOf(offer.id);
    if (at >= 0) PICKED.splice(at, 1); else PICKED.push(offer.id);
    render();
  });
  card.disabled = !selectable;
  if (selectable) card.setAttribute("aria-pressed", picked ? "true" : "false");
  const head = el("div", "head");
  add(head, el("span", "t", offer.title));
  if (selectable) add(head, el("span", "tick", "✓"));
  else add(head, el("span", "chip grey",
    offer.future ? "FUTURE OPTION" : offer.built ? "NOT ON THIS PHONE" : "COMING NEXT"));
  if (offer.future) card.classList.add("future");
  add(card, head);
  if (offer.kind === "advanced") {
    add(card, el("span", "chip indigo", "ADVANCED · AI MODEL"));
  }
  add(card, el("span", "s", offer.summary));
  add(card, el("span", "h", offer.future ?
    "Not part of this version yet." : offer.headline));
  const fit = el("div", "fit");
  add(fit, el("span", null, "Fit"),
    track(offer.score, offer.status === "limited" ? "amber" : ""),
    el("span", null, offer.status === "limited" ? "Limited" :
      offer.score >= 85 ? "Great" : offer.score >= 60 ? "Good" : "Tight"));
  add(card, fit);
  return card;
}

function screenServices(root) {
  const st = STATE;
  if (!st.profile) { needConnect(root, "What it can do for you"); return; }
  const offers = st.offers;
  add(root, heading("What it can do for you",
    "Only what this phone can run well. Pick any. The bar shows how " +
    "well each one suits the phone."));
  if (!offers) {
    add(root, note("warn", "⚠", null, "The offers could not be worked out."));
    return;
  }
  if (offers.verdict !== "usable") {
    add(root, note("stop", "⊘", "Nothing can run on this phone.",
      (offers.reasons || []).join(" ")));
    return;
  }
  const grid = el("div", "grid cards");
  const refused = [];
  offers.services.forEach(function (offer, i) {
    if (offer.status === "not_possible") { refused.push(offer); return; }
    add(grid, offerCard(offer, i));
  });
  add(root, grid);
  refused.forEach(function (offer) {
    add(root, note("up", "⊘", "Not offered: " + offer.title + ".",
      (offer.reasons || [offer.headline]).join(" ")));
  });
  add(root, note("info up", "→", ROUTE_TITLES[offers.route] + ".",
    offers.route_text));

  const view = st.outputs.plan;
  if (view.status === "current" && view.data && sameSelection()) {
    renderPlan(root, view.data);
    const det = outputDetails("Plan", view);
    if (det && wantsDetails()) add(root, det);
  }
}

function sameSelection() {
  const st = STATE;
  if (!st.selection) return false;
  const a = st.selection.services.slice().sort().join(",");
  const b = currentPick().slice().sort().join(",");
  return a === b;
}

async function onSelection() {
  const services = currentPick().slice();
  if (services.length === 0) {
    setStatus("Pick at least one service.");
    return false;
  }
  setStatus("Checking what fits.");
  const res = await api("POST", "/api/selection", { services: services });
  if (res.status !== 200 || !res.data) { fail(res, "Check failed."); return false; }
  STATE = res.data.state;
  const plan = STATE.outputs.plan.data || {};
  setStatus(plan.selection_result === "refused" ?
    "That selection does not fit." : "Selection checked.");
  render();
  return plan.selection_result !== "refused";
}

function renderPlan(root, plan) {
  const result = plan.selection_result || "unknown";
  const choice = plan.fit.model_choice || {};
  if (result === "refused") {
    add(root, note("stop up", "⊘", "This selection does not fit.",
      (plan.fit.reasons || []).join(" ")));
  } else if (choice.status === "chosen") {
    add(root, note("good up", "✓", "Fits.",
      "The assistant will use the model " + choice.model_id +
      ", the largest one this phone can run."));
  } else if (choice.status === "cloud_only") {
    add(root, note("good up", "✓", "Fits.",
      "The assistant will answer through your own cloud key, because " +
      "no on-phone model fits this phone."));
  } else if (result === "unconfirmed") {
    add(root, note("warn up", "⚠", "Probably fits.",
      (plan.fit.reasons || []).join(" ")));
  } else {
    add(root, note("good up", "✓", "Fits.", "Everything you picked runs on this phone."));
  }
  if (!wantsDetails()) return;
  const panel = el("div", "panel");
  add(panel, el("h3", null, "The numbers"));
  const wrap = el("div", "table-scroll");
  const table = el("table", "facts");
  const hr = el("tr");
  ["Check", "Status", "Numbers"].forEach(function (t) { add(hr, el("th", null, t)); });
  add(table, hr);
  (plan.fit.checks || []).forEach(function (check) {
    const row = el("tr");
    let numbers = check.reason || "";
    if (typeof check.have_bytes === "number" &&
        typeof check.needed_bytes === "number") {
      numbers = "have " + fmtBytes(check.have_bytes) + ", need " +
        fmtBytes(check.needed_bytes);
    }
    add(row, el("td", null, check.name || ""),
      el("td", null, check.status || ""), el("td", "value", numbers));
    add(table, row);
  });
  add(panel, add(wrap, table));
  (plan.strategies || []).forEach(function (s) {
    add(panel, el("p", "faint", "approach " + (s.id || "") + ": " + (s.status || "")));
  });
  add(panel, el("p", "faint", "Sizes are planning estimates. Provenance: " +
    (plan.provenance || "unknown") + "."));
  if (plan.notes) {
    add(panel, el("p", "muted", plan.notes));
    add(panel, el("p", "faint", "model explanation, not checked."));
  }
  add(root, panel);
}

/* -- research ----------------------------------------------------- */

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

function renderM5Saved(col, mv) {
  const data = mv.data;
  const list = el("ol", "timeline");
  (data.pages || []).forEach(function (page) {
    const li = el("li");
    add(li, el("span", "t", "Read an official page"),
      el("span", "d", "Page " + (page.id || "") + ", " + page.byte_size +
        " bytes; excerpt ranges " + JSON.stringify(page.excerpt_ranges) + "."),
      el("span", "src", page.url));
    add(list, li);
  });
  (data.drafts || []).forEach(function (draft) {
    const ok = draft.verdict === "ACCEPTED";
    const li = el("li", ok ? "" : "amber");
    add(li, el("span", "t", "Draft " + draft.index + ": " +
      (ok ? "accepted by our checks" : "rejected by our checks")));
    (draft.rules || []).forEach(function (rule) {
      add(li, el("span", "d", rule.rule + ": " + rule.status + "." +
        ((rule.reasons || []).length ? " " + rule.reasons.join(" ") : "")));
    });
    const usage = draft.usage || {};
    add(li, el("span", "faint", "Tokens: prompt " + usage.prompt_tokens +
      ", completion " + usage.completion_tokens + ", reasoning " +
      usage.reasoning_tokens + "."));
    add(list, li);
  });
  add(col, list);
  const result = data.result === "do_not_install" ? "Do not install" :
    String(data.result);
  add(col, el("p", null, "Result: " + result + "."));
  if (data.refusal) {
    const box = el("div", "note stop");
    add(box, el("span", "ico", "⊘"));
    const body = el("div", "stack");
    add(body, el("b", null, "Refusal: " + data.refusal.reason),
      el("span", "muted", "Quote:"),
      el("span", "quote", data.refusal.quote),
      el("span", "src", "Source: " + data.refusal.url + " (" +
        data.refusal.rule + ", " + data.refusal.fetch_id + ")."));
    add(col, add(box, body));
  }
  const model = data.model || {};
  add(col, el("p", "muted", "Model: " + model.id + " (" + model.provider + ")."));
  add(col, el("p", "faint",
    "Install-routing proposal: not a validated install procedure."));
  add(col, m5InputDetails(mv));
}

function screenResearch(root) {
  const st = STATE;
  const split = el("section", "split top-align");
  const col = el("div", "col");
  add(col, heading("Researching how to do it safely",
    "A model reads the official pages and proposes how to install. " +
    "Our code checks every quote against its source and can say no."));
  const mv = st.outputs.m5;
  if (mv.status === "saved" && mv.data) {
    const head = el("div", "row");
    add(head, el("h3", null, mv.message + "."), el("span", "chip sim", "SIMULATED"));
    add(col, head);
    renderM5Saved(col, mv);
  } else {
    const list = el("ol", "timeline");
    const a = el("li", "grey");
    add(a, el("span", "t", "Recorded research"),
      el("span", "d", st.outputs.research.message + "."));
    const b = el("li", "grey");
    add(b, el("span", "t", "Install plan"), el("span", "d", mv.message + "."));
    add(list, a, b);
    add(col, list);
    if (mv.status === "outdated") add(col, m5InputDetails(mv));
  }
  const side = el("div", "side up d2");
  const panel = el("div", "panel");
  add(panel, el("h3", null, "Live research"));
  add(panel, el("p", "muted",
    "A live run reads the official pages again with your own model " +
    "key. " + st.live.message + "."));
  const live = button("ghost", "Go live");
  live.disabled = true;
  live.title = st.live.message;
  add(panel, live);
  add(side, panel);
  add(root, add(split, col, side));
}

/* -- build -------------------------------------------------------- */

function latestBuildJob() {
  const ids = Object.keys(STATE.jobs).filter(function (id) {
    return STATE.jobs[id].step === "build";
  }).sort();
  if (ids.length === 0) return null;
  return { id: ids[ids.length - 1], job: STATE.jobs[ids[ids.length - 1]] };
}

function screenBuild(root) {
  const st = STATE;
  const planView = st.outputs.plan;
  const buildView = st.outputs.build;
  const split = el("section", "split top-align");
  const col = el("div", "col");
  add(col, heading("Building your phone's software",
    "This happens on your computer. The phone isn't touched yet."));
  add(root, add(split, col));
  if (planView.status !== "current") {
    add(col, note("warn", "⚠", null, planView.status === "missing" ?
      "Confirm services first." : "The plan is out of date: re-run Services."));
    add(col, button("ghost", "Go to Services", function () { gotoStep("services"); }));
    return;
  }
  const plan = planView.data;
  if (plan.selection_result === "refused") {
    add(col, note("stop", "⊘", "Build blocked: the plan refuses this phone.",
      (plan.fit.reasons || []).join(" ")));
    return;
  }
  if (plan.build_allowed !== "emulator_only") {
    add(col, note("stop", "⊘", "Build blocked:",
      "the plan does not allow a build."));
    return;
  }
  const found = latestBuildJob();
  const running = found && found.job.status === "running";
  const done = buildView.status === "current" && buildView.data;
  const stages = [
    ["Check the plan", "selection " + (plan.handoff.services || []).join(", ")],
    ["Build the phone app for your choices", ""],
    ["Package it for this phone", ""],
    ["Check the result", ""]];
  stages.forEach(function (row, i) {
    const cls = done ? "done" : running ? (i === 0 ? "done" : i === 1 ? "now" : "") :
      (found && found.job.status === "failed" && i === 1 ? "fail" : "");
    const line = el("div", "stage " + cls);
    add(line, el("span", "m", done || (running && i === 0) ? "✓" : String(i + 1)),
      el("span", "x", row[0]), el("span", "meta", row[1]));
    add(col, line);
  });
  if (buildView.status === "stale") {
    add(col, note("warn", "⚠", "Out of date.", buildView.message ||
      "Re-run the build to refresh."));
  }
  const go = button("primary", done ? "Build again" : "Build", onBuild);
  go.disabled = !!running;
  const row = el("div", "row");
  add(col, add(row, go, el("span", "faint",
    "Every build is made fresh for this plan.")));

  const side = el("div", "side up d2");
  if (found) {
    const panel = el("div", "panel");
    add(panel, el("span", "muted", "Job " + found.id + ": " + found.job.status + "."));
    if (found.job.reason) add(panel, el("span", "muted", found.job.reason));
    add(side, panel);
    const log = el("pre", "log", "");
    log.id = "build-log";
    add(side, log);
    api("GET", "/api/jobs/" + found.id, null).then(function (res) {
      if (res.status === 200 && res.data) paintProgress(res.data.progress || []);
    });
    if (running) startBuildPoll();
  }
  if (done) renderBuildResult(side, buildView.data);
  add(split, side);
  if (done && wantsDetails()) {
    const det = outputDetails("Build", buildView);
    if (det) add(root, det);
  }
}

async function onBuild() {
  setStatus("Starting the build.");
  const res = await api("POST", "/api/build", {});
  if (res.status !== 202 || !res.data) { fail(res, "Build refused."); return; }
  STATE = res.data.state;
  setStatus("Build running: " + res.data.build_id + ".");
  render();
}

function startBuildPoll() {
  if (POLL) clearInterval(POLL);
  document.getElementById("screen").classList.add("loading");
  POLL = setInterval(async function () {
    const found = latestBuildJob();
    if (!found) { clearInterval(POLL); POLL = null; return; }
    const res = await api("GET", "/api/jobs/" + found.id, null);
    if (res.status !== 200 || !res.data) return;
    paintProgress(res.data.progress || []);
    if (res.data.job.status !== "running") {
      clearInterval(POLL);
      POLL = null;
      document.getElementById("screen").classList.remove("loading");
      const st = await api("GET", "/api/state", null);
      if (st.status === 200 && st.data) {
        CSRF = st.data.csrf;
        STATE = st.data.state;
        setStatus("Build " + res.data.job.status + ".");
        render();
      }
    }
  }, 2000);
}

function paintProgress(lines) {
  const log = document.getElementById("build-log");
  if (!log) return;
  log.textContent = lines.join("\n");
  log.scrollTop = log.scrollHeight;
}

function renderBuildResult(side, data) {
  const summary = data.summary || {};
  const panel = el("div", "panel");
  add(panel, el("h3", null, "Build done"));
  add(panel, el("span", "muted", "Build " + (summary.build_id || "") + " for " +
    (summary.selection || []).join(", ") + "."));
  if (summary.image && summary.partition_limit_bytes) {
    const top = el("div", "between");
    add(top, el("span", "muted", "New system"),
      el("span", "mono", fmtSize(summary.image.bytes)));
    add(panel, top, track(100 * summary.image.bytes /
      summary.partition_limit_bytes, "", true));
    add(panel, el("span", "faint", "Limit " +
      fmtSize(summary.partition_limit_bytes) + ". " + summary.image.path));
  }
  if (summary.app) {
    const top = el("div", "between");
    add(top, el("span", "muted", "Phone app"),
      el("span", "mono", fmtSize(summary.app.bytes)));
    add(panel, top);
    if (summary.route_text) add(panel, el("span", "muted", summary.route_text));
    (summary.notes || []).forEach(function (text) {
      add(panel, el("span", "muted", text));
    });
    add(panel, el("span", "faint", "Saved in " + summary.folder));
  }
  if (summary.gz) {
    add(panel, el("span", "faint", "Transfer file " + summary.gz.path + ": " +
      fmtSize(summary.gz.bytes) + "."));
  }
  (data.checks || []).forEach(function (check) {
    add(panel, el("span", "muted", "✓ " + check.check + " (" +
      (check.services || []).join(", ") + ")"));
  });
  add(side, panel);
}

/* -- install / boot ---------------------------------------------- */

async function screenBoot(root) {
  const st = STATE;
  const col = el("section", "col wide");
  add(col, heading("Install it on the phone",
    st.boot_target + ". Nothing changes on the phone until you run these steps."));
  add(root, col);
  const view = st.outputs.build;
  if (view.status !== "current") {
    add(col, note("warn", "⚠", null, view.status === "missing" ?
      "Build first." : "The build is out of date: re-run Build."));
    return;
  }
  const key = view.output_sha256;
  if (BOOT_CACHE.key !== key) {
    add(col, el("p", "muted pulse", "Loading the steps."));
    const res = await api("GET", "/api/boot", null);
    if (res.status !== 200 || !res.data) { fail(res, "Boot steps failed."); return; }
    BOOT_CACHE = { key: key, text: res.data.steps };
    if (STEP === "boot") render();
    return;
  }
  add(col, note("good", "→", "Installs what was built for this phone.",
    "Run the steps on your computer with the phone on the cable. " +
    "The last part shows how to undo it."));
  add(col, button("ghost", "Copy the steps", function () {
    copyText(BOOT_CACHE.text);
  }));
  add(col, el("pre", "steps", BOOT_CACHE.text));
  if (!(view.data && view.data.summary && view.data.summary.kind === "phone_package")) {
    const real = el("div", "panel");
    add(real, el("h3", null, "On a real phone"),
      el("p", "muted", st.outputs.m5.message + "."));
    add(col, real);
  }
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

/* -- done --------------------------------------------------------- */

function screenDone(root) {
  const st = STATE;
  const col = el("section", "col wide");
  const view = st.outputs.build;
  if (view.status === "current" && view.data) {
    const summary = view.data.summary || {};
    add(col, heading("All set", "Built " + (summary.build_id || "") +
      " for " + (summary.selection || []).join(", ") + "."));
    if (summary.image) {
      add(col, el("p", "muted", summary.image.path + ": " +
        fmtSize(summary.image.bytes) + "."));
    }
  } else {
    add(col, heading("Not finished yet",
      "Nothing built yet. Work through the steps above."));
  }
  const link = el("div", "panel");
  add(link, el("h3", null, "Your phone's page"),
    el("p", "muted", "The phone's page link " + st.phone_link.message + "."));
  add(col, link);
  add(root, col);
}

/* -- navigation and render --------------------------------------- */

function nextState() {
  const st = STATE;
  const usable = !st.offers || st.offers.verdict === "usable";
  if (STEP === "welcome") return { label: "Start", ok: true };
  if (STEP === "connect") return { label: "Continue", ok: !!st.profile };
  if (STEP === "questions") return { label: "Continue", ok: !!st.answers };
  if (STEP === "phone") return { label: "See what it can do", ok: !!st.profile && usable };
  if (STEP === "services") {
    return { label: "Continue", ok: !!st.profile && usable && currentPick().length > 0 };
  }
  if (STEP === "research") return { label: "Continue to build", ok: true };
  if (STEP === "build") return { label: "Continue", ok: st.outputs.build.status === "current" };
  if (STEP === "boot") return { label: "Finish", ok: true };
  return { label: "", ok: false };
}

async function onNext() {
  const at = STEPS.indexOf(STEP);
  if (STEP === "services") {
    const planView = STATE.outputs.plan;
    const fresh = planView.status === "current" && sameSelection() &&
      planView.data && planView.data.selection_result !== "refused";
    if (!fresh) {
      const ok = await onSelection();
      if (!ok) return;
    }
  }
  if (at < STEPS.length - 1) gotoStep(STEPS[at + 1]);
}

function render() {
  const at = STEPS.indexOf(STEP);
  document.querySelectorAll("#rail-list button").forEach(function (b) {
    const mine = STEPS.indexOf(b.getAttribute("data-step"));
    if (mine === at) b.setAttribute("aria-current", "step");
    else b.removeAttribute("aria-current");
    b.classList.toggle("done", mine >= 0 && mine < at);
  });
  const root = elClear("screen");
  const back = document.getElementById("nav-back");
  const next = document.getElementById("nav-next");
  if (!STATE) {
    add(root, el("p", "muted", "Loading."));
    back.hidden = true;
    next.hidden = true;
    return;
  }
  if (STEP === "welcome") screenWelcome(root);
  else if (STEP === "connect") screenConnect(root, false);
  else if (STEP === "questions") screenQuestions(root);
  else if (STEP === "phone") screenPhone(root);
  else if (STEP === "services") screenServices(root);
  else if (STEP === "research") screenResearch(root);
  else if (STEP === "build") screenBuild(root);
  else if (STEP === "boot") screenBoot(root);
  else if (STEP === "done") screenDone(root);
  const ns = nextState();
  back.hidden = at === 0;
  next.hidden = !ns.label;
  next.textContent = ns.label;
  next.disabled = !ns.ok;
}

async function signOut() {
  await api("POST", "/api/signout", {});
  CSRF = "";
  STATE = null;
  if (POLL) { clearInterval(POLL); POLL = null; }
  render();
  const root = elClear("screen");
  add(root, el("p", "muted",
    "Signed out. The start link works once; restart the server for " +
    "a new link."));
  setStatus("Signed out.");
}

async function boot() {
  document.querySelectorAll("#rail-list button").forEach(function (b) {
    b.addEventListener("click", function () {
      gotoStep(b.getAttribute("data-step"));
    });
  });
  document.getElementById("signout").addEventListener("click", signOut);
  document.getElementById("nav-back").addEventListener("click", function () {
    const at = STEPS.indexOf(STEP);
    if (at > 0) gotoStep(STEPS[at - 1]);
  });
  document.getElementById("nav-next").addEventListener("click", onNext);
  try {
    const res = await api("GET", "/api/state", null);
    if (res.status !== 200 || !res.data) {
      const root = elClear("screen");
      add(root, note("warn", "⚠", null,
        "Could not load. Sign in again from the terminal link."));
      return;
    }
    CSRF = res.data.csrf;
    STATE = res.data.state;
    setStatus("Ready.");
    render();
  } catch (e) {
    const root = elClear("screen");
    add(root, note("warn", "⚠", null, "Could not reach the server."));
  }
}

document.addEventListener("DOMContentLoaded", boot);
