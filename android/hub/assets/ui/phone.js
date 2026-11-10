/* The phone's own screen: settings and controls only. Everything you
   actually use (files, chat, passwords) lives on the page you open
   from a computer. Text goes in through textContent only. */
(function () {
  'use strict';
  var SVG = 'http://www.w3.org/2000/svg';
  var ICONS = {
    files: 'M4 6.5A1.5 1.5 0 0 1 5.5 5h4l2 2.5h7A1.5 1.5 0 0 1 20 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z',
    assistant: 'M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.500l5.1-1.9zM18.5 15.500l.8 2.200 2.200.8-2.200.8-.8 2.200-.8-2.200-2.200-.8 2.200-.8z',
    vault: 'M7 10.500V8a5 5 0 0 1 10 0v2.500M6.500 10.500h11A1.500 1.500 0 0 1 19 12v7a1.500 1.500 0 0 1-1.500 1.500h-11A1.500 1.500 0 0 1 5 19v-7a1.500 1.500 0 0 1 1.500-1.500zM12 14.500v2.500',
    sys: 'M12 4a8 8 0 1 0 0 16zM12 4a8 8 0 0 1 0 16',
    back: 'M14.500 5.500L8 12l6.500 6.500'
  };
  var TILE_CLASS = { files: '', assistant: 'indigo', vault: 'amber', sys: 'grey' };
  var SHORT = { files: 'Files', assistant: 'AI assistant', vault: 'Passwords' };
  var view = document.getElementById('view');
  var st = null;
  var ui = { view: 'home', netAsk: false, boot: false, test: 'idle', testMs: 0, busy: {} };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }
  function icon(name) {
    var svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.7');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    var path = document.createElementNS(SVG, 'path');
    path.setAttribute('d', ICONS[name]);
    svg.appendChild(path);
    return svg;
  }
  function button(cls, text, fn) {
    var b = el('button', cls, text);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  }
  function api(method, path, body) {
    var opts = { method: method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') {
      opts.headers['X-Hub'] = '1';
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body || {});
    }
    return fetch(path, opts).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) { throw new Error(data.error || 'failed'); }
        return data;
      });
    });
  }
  function apply(next) {
    if (next && next.services) { st = next; }
    render();
  }
  function refresh() {
    return api('GET', '/api/state').then(apply).catch(function () {});
  }
  function service(id) {
    for (var i = 0; i < st.services.length; i++) {
      if (st.services[i].id === id) { return st.services[i]; }
    }
    return null;
  }
  function two(n) { return n < 10 ? '0' + n : String(n); }
  function clockText() {
    var d = new Date();
    return two(d.getHours()) + ':' + two(d.getMinutes());
  }
  function dateText() {
    var days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    var months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    var d = new Date();
    return days[d.getDay()] + ' ' + d.getDate() + ' ' + months[d.getMonth()];
  }
  function size(bytes) {
    var gb = bytes / 1073741824;
    if (gb >= 10) { return Math.round(gb) + ' GB'; }
    if (gb >= 1) { return gb.toFixed(1) + ' GB'; }
    return Math.max(1, Math.round(bytes / 1048576)) + ' MB';
  }
  function stateWord(svc) {
    if (ui.busy[svc.id]) { return 'Restarting…'; }
    return svc.running ? 'Running' : 'Stopped';
  }
  function chip(svc) {
    var c = el('span', 'chip' + (ui.busy[svc.id] ? ' busy' : svc.running ? '' : ' warn'), stateWord(svc));
    return c;
  }
  function row(label, right) {
    var r = el('div', 'row');
    r.appendChild(el('span', '', label));
    if (typeof right === 'string') { right = el('span', 'muted', right); }
    r.appendChild(right);
    return r;
  }
  function go(name) {
    ui.view = name; ui.netAsk = false; ui.boot = false; ui.test = 'idle';
    render();
  }
  function act(id, action) {
    if (action === 'restart') { ui.busy[id] = true; render(); }
    api('POST', '/api/service', { id: id, action: action }).then(function (next) {
      if (action === 'restart') {
        setTimeout(function () { ui.busy[id] = false; apply(next); }, 900);
      } else { apply(next); }
    }).catch(function () { ui.busy[id] = false; refresh(); });
  }
  function controls(svc, extra) {
    var pair = el('div', 'two');
    pair.appendChild(button('btn', svc.running ? 'Stop' : 'Start', function () {
      act(svc.id, svc.running ? 'stop' : 'start');
    }));
    pair.appendChild(extra || button('btn', 'Restart', function () { act(svc.id, 'restart'); }));
    return pair;
  }

  function renderHome() {
    var home = el('div', 'home');
    var box = el('div', 'clockbox up');
    box.appendChild(el('span', 'clock', clockText()));
    box.appendChild(el('span', 'date', dateText()));
    var on = 0;
    st.services.forEach(function (s) { if (s.running) { on++; } });
    var all = on === st.services.length;
    box.appendChild(el('span', 'allok' + (all ? '' : ' warn'),
      all ? 'All services running' : on + ' of ' + st.services.length + ' services running'));
    home.appendChild(box);
    var tiles = el('div', 'tiles');
    var list = st.services.map(function (s) {
      var note = stateWord(s);
      if (s.id === 'assistant' && st.assistant) {
        note += ' · ' + (st.assistant.mode === 'local' ? 'on the phone' : 'cloud');
      }
      if (s.id === 'vault' && st.vault && s.running) {
        note = !st.vault.exists ? 'Not set up yet' : st.vault.unlocked ? 'Open on your computer' : 'Locked';
      }
      return [s.id, SHORT[s.id], note];
    });
    list.push(['sys', 'Status', st.network.on ? 'On Wi-Fi' : 'Cable only']);
    list.forEach(function (t, i) {
      var tile = button('tile up d' + Math.min(i + 1, 4), null, function () { go(t[0]); });
      var ic = el('span', 'icon ' + TILE_CLASS[t[0]]);
      ic.appendChild(icon(t[0]));
      tile.appendChild(ic);
      var name = el('span', 'tname');
      name.appendChild(el('b', '', t[1]));
      name.appendChild(el('span', '', t[2]));
      tile.appendChild(name);
      tiles.appendChild(tile);
    });
    home.appendChild(tiles);
    view.appendChild(home);
  }

  function header(title) {
    var head = el('div', 'head');
    var back = button('back', null, function () { go('home'); });
    back.setAttribute('aria-label', 'Back to home');
    back.appendChild(icon('back'));
    head.appendChild(back);
    head.appendChild(el('span', 'title', title));
    view.appendChild(head);
  }
  function columns() {
    var cols = el('div', 'cols');
    var a = el('section', 'col up');
    var b = el('section', 'col up d1');
    cols.appendChild(a); cols.appendChild(b);
    view.appendChild(cols);
    return [a, b];
  }

  function renderFiles() {
    var svc = service('files');
    header('Files');
    var c = columns();
    c[0].appendChild(row('Service', chip(svc)));
    c[0].appendChild(controls(svc));
    var code = st.code || '';
    c[0].appendChild(row('Access code', el('span', 'code', code.slice(0, 3) + ' ' + code.slice(3))));
    c[0].appendChild(button('btn', 'New access code', function () {
      api('POST', '/api/code/new').then(apply);
    }));
    var card = el('div', 'card');
    var line = el('div', 'cardrow');
    line.appendChild(el('span', '', 'Storage'));
    line.appendChild(el('span', '', size(st.storage.free) + ' free'));
    card.appendChild(line);
    var bar = el('span', 'bar');
    var fill = el('span');
    var used = st.storage.total > 0 ? 1 - st.storage.free / st.storage.total : 0;
    fill.style.width = Math.max(2, Math.round(used * 100)) + '%';
    bar.appendChild(fill);
    card.appendChild(bar);
    c[1].appendChild(card);
    c[1].appendChild(el('span', 'hint',
      'Browse, upload and play from the page on your computer, through the cable. Nothing opens on this screen.'));
  }

  function renderAssistant() {
    var svc = service('assistant');
    var a = st.assistant;
    var local = a.mode === 'local';
    header('AI assistant');
    var c = columns();
    c[0].appendChild(row('Service', chip(svc)));
    var seg = el('div', 'seg');
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', 'Model');
    var bl = button(local ? 'on' : '', 'On the phone', function () { mode('local'); });
    if (!a.local_name) { bl.disabled = true; }
    seg.appendChild(bl);
    seg.appendChild(button(local ? '' : 'on', 'Cloud', function () { mode('cloud'); }));
    c[0].appendChild(seg);
    var blocked = !local && (!a.key_set || !st.network.on);
    var hint;
    if (local) {
      hint = a.local_name + ' runs on this phone. Works with no network.';
    } else if (!a.key_set) {
      hint = a.cloud_name + '. Add your key first.';
    } else if (!st.network.on) {
      hint = a.cloud_name + '. Needs the network: turn it on in Status.';
    } else {
      hint = a.cloud_name + '. Uses your own key.';
    }
    if (!a.local_name && !local) {
      hint += ' This phone is too small to run a model itself.';
    }
    c[0].appendChild(el('span', 'hint small' + (blocked ? ' warn' : ''), hint));
    var label = ui.test === 'busy' ? 'Testing…' : ui.test === 'ok'
      ? '✓ Answered · ' + (ui.testMs / 1000).toFixed(1) + ' s'
      : ui.test === 'fail' ? 'Can’t reach it' : 'Self-test';
    c[0].appendChild(controls(svc, button('btn', label, function () {
      if (ui.test === 'busy') { return; }
      if (!svc.running || blocked) { ui.test = 'fail'; render(); return; }
      ui.test = 'busy'; render();
      api('POST', '/api/assistant/ask', { question: 'Reply with the single word OK.' })
        .then(function (r) { ui.test = 'ok'; ui.testMs = r.ms; render(); })
        .catch(function () { ui.test = 'fail'; render(); });
    })));
    c[1].appendChild(row('Cloud key', a.key_set ? 'Set (hidden)' : 'Not set'));
    if (a.key_set) {
      c[1].appendChild(button('btn', 'Remove key', function () {
        api('DELETE', '/api/assistant/key').then(apply);
      }));
    } else {
      var add = button('btn', 'Add key from your computer', function () {});
      add.disabled = true;
      c[1].appendChild(add);
    }
    c[1].appendChild(el('span', 'hint',
      'Chat from the page on your computer. The phone only runs the model.'));
    function mode(m) {
      ui.test = 'idle';
      api('POST', '/api/assistant/mode', { mode: m }).then(apply).catch(refresh);
    }
  }

  function renderVault() {
    var svc = service('vault');
    var v = st.vault;
    header('Passwords');
    var c = columns();
    c[0].appendChild(row('Service', chip(svc)));
    c[0].appendChild(controls(svc));
    var word = !v.exists ? 'Not set up yet' : v.unlocked ? 'Open on your computer' : 'Locked';
    c[1].appendChild(row('Vault', word));
    var lock = button('btn', 'Lock now', function () {
      api('POST', '/api/vault/lock').then(apply).catch(refresh);
    });
    if (!v.unlocked || !svc.running) { lock.disabled = true; }
    c[1].appendChild(lock);
    c[1].appendChild(el('span', 'hint',
      'Passwords open on the page on your computer, never on this screen. The vault locks itself after five quiet minutes.'));
  }

  function renderSys() {
    header('Status');
    var c = columns();
    var net = st.network;
    if (net.can_switch) {
      var r = button('row', null, function () {
        if (net.on) { ui.netAsk = true; render(); } else { device('network_on'); }
      });
      r.appendChild(el('span', '', 'Network'));
      r.appendChild(el('span', 'act', net.on ? 'Turn off' : 'Turn on'));
      c[0].appendChild(r);
    } else {
      c[0].appendChild(row('Network', net.on ? 'On Wi-Fi' : 'Cable only'));
    }
    if (ui.netAsk) {
      var ask = el('div', 'ask up');
      ask.appendChild(el('b', '', 'Turn off the network?'));
      ask.appendChild(el('span', '',
        'The phone leaves Wi-Fi. Everything still works through the cable, but the cloud assistant stops.'));
      var pair = el('div', 'two');
      pair.appendChild(button('btn', 'Cancel', function () { ui.netAsk = false; render(); }));
      pair.appendChild(button('btn warnfill', 'Cable only', function () {
        ui.netAsk = false; device('network_off');
      }));
      ask.appendChild(pair);
      c[0].appendChild(ask);
    }
    var b = st.battery;
    c[0].appendChild(row('Battery', b.percent < 0 ? 'Unknown'
      : b.percent + '% · ' + (b.plugged ? 'plugged in' : 'on battery')));
    c[0].appendChild(row('Storage', size(st.storage.free) + ' free'));
    if (st.can_reboot) {
      c[1].appendChild(button('btn', ui.boot ? 'Tap again to reboot' : 'Reboot phone', function () {
        if (ui.boot) { ui.boot = false; device('reboot'); go('home'); } else { ui.boot = true; render(); }
      }));
    }
    var note = el('span', 'hint small');
    note.appendChild(document.createTextNode('Build '));
    note.appendChild(el('span', 'mono', st.build_id));
    note.appendChild(document.createTextNode('. Light on battery: nothing runs that you didn’t choose.'));
    c[1].appendChild(note);
    function device(action) {
      api('POST', '/api/device', { action: action }).then(apply).catch(refresh);
    }
  }

  function render() {
    if (!st) { return; }
    if (ui.view !== 'home' && ui.view !== 'sys' && !service(ui.view)) { ui.view = 'home'; }
    while (view.firstChild) { view.removeChild(view.firstChild); }
    var home = ui.view === 'home';
    document.getElementById('logo').className = home ? 'logo' : 'logo hide';
    var top = document.getElementById('topclock');
    top.className = home ? 'mono hide' : 'mono';
    top.textContent = clockText();
    document.getElementById('net').className = 'chip' + (st.network.on ? '' : ' warn');
    document.getElementById('netlabel').textContent = st.network.on ? 'Wi-Fi' : 'Cable only';
    if (home) { renderHome(); }
    else if (ui.view === 'files') { renderFiles(); }
    else if (ui.view === 'assistant') { renderAssistant(); }
    else if (ui.view === 'vault') { renderVault(); }
    else { renderSys(); }
  }

  refresh();
  setInterval(function () {
    if (ui.view === 'home' && !ui.netAsk) { refresh(); } else {
      api('GET', '/api/state').then(function (next) { st = next; }).catch(function () {});
    }
  }, 15000);
}());
