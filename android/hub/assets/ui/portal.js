/* The phone's management page, opened from a computer: files and
   media, the assistant chat and the password vault. Text goes in
   through textContent only; nothing here builds markup from data. */
(function () {
  'use strict';
  var SVG = 'http://www.w3.org/2000/svg';
  var ICONS = {
    file: 'M7 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9.500A1.500 1.500 0 0 1 5.500 19V5A1.500 1.500 0 0 1 7 3.500zM14 3.500v4h4',
    photo: 'M5.500 5h13A1.500 1.500 0 0 1 20 6.500v11a1.500 1.500 0 0 1-1.500 1.500h-13A1.500 1.500 0 0 1 4 17.500v-11A1.500 1.500 0 0 1 5.500 5zM4 16l4.500-4.500 3.500 3.500 2.500-2.500L20 18M15.500 9.500h.01',
    music: 'M9 18V6l10-2v12M9 18a2.500 2.500 0 1 1-5 0 2.500 2.500 0 0 1 5 0zM19 16a2.500 2.500 0 1 1-5 0 2.500 2.500 0 0 1 5 0z',
    video: 'M5.500 5.500h9A1.500 1.500 0 0 1 16 7v10a1.500 1.500 0 0 1-1.500 1.500h-9A1.500 1.500 0 0 1 4 17V7a1.500 1.500 0 0 1 1.500-1.500zM16 10l4.500-2.500v9L16 14',
    upload: 'M12 16V5M7.500 9.500L12 5l4.500 4.500M5 19h14',
    key: 'M8 15.500a3.500 3.500 0 1 0 0-7 3.500 3.500 0 0 0 0 7zM11.500 12H20M17 12v3M20 12v2'
  };
  var TABS = { files: 'Files and media', assistant: 'Assistant', vault: 'Passwords' };
  var app = document.getElementById('app');
  var picker = document.getElementById('picker');
  var st = null;
  var ui = {
    tab: null, filter: 'all', files: [], fresh: {}, upload: null, done: false,
    open: null, confirm: null, chat: [], thinking: false, draft: '',
    entries: null, pick: null, search: '', note: '', noteOk: false, show: false
  };

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
    svg.setAttribute('stroke-width', '1.6');
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
  function input(type, placeholder, value) {
    var i = el('input');
    i.type = type;
    if (placeholder) { i.placeholder = placeholder; }
    if (value) { i.value = value; }
    i.autocomplete = 'off';
    i.spellcheck = false;
    return i;
  }
  function field(label, control) {
    var l = el('label', '', label);
    l.appendChild(control);
    return l;
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
        if (res.status === 401) { st = null; render(); }
        if (!res.ok) { throw new Error(data.error || 'Something went wrong.'); }
        return data;
      });
    });
  }
  function size(bytes) {
    if (bytes >= 1073741824) { return (bytes / 1073741824).toFixed(1) + ' GB'; }
    if (bytes >= 1048576) { return Math.round(bytes / 1048576) + ' MB'; }
    if (bytes >= 1024) { return Math.round(bytes / 1024) + ' KB'; }
    return bytes + ' B';
  }
  function gb(bytes) {
    var v = bytes / 1073741824;
    return (v >= 10 ? Math.round(v) : v.toFixed(1)) + ' GB';
  }
  function service(id) {
    if (!st) { return null; }
    for (var i = 0; i < st.services.length; i++) {
      if (st.services[i].id === id) { return st.services[i]; }
    }
    return null;
  }
  function say(text, ok) { ui.note = text || ''; ui.noteOk = !!ok; }
  function fail(err) { say(err.message); render(); }

  function load() {
    return api('GET', '/api/state').then(function (next) {
      st = next;
      if (!ui.tab || !service(ui.tab)) {
        ui.tab = st.services.length ? st.services[0].id : null;
      }
      return enter();
    }).catch(function () { render(); });
  }
  function enter() {
    say('');
    var svc = service(ui.tab);
    if (svc && svc.running && ui.tab === 'files') { return loadFiles(); }
    if (svc && svc.running && ui.tab === 'vault') { return loadVault(); }
    render();
    return null;
  }
  function loadFiles() {
    return api('GET', '/api/files').then(function (data) {
      ui.files = data.files.sort(function (a, b) { return b.changed - a.changed; });
      render();
    }).catch(fail);
  }
  function loadVault() {
    return api('GET', '/api/state').then(function (next) {
      st = next;
      if (!st.vault.unlocked) { ui.entries = null; ui.pick = null; render(); return null; }
      return api('GET', '/api/vault/entries').then(function (data) {
        ui.entries = data.entries.sort(function (a, b) {
          return a.title.toLowerCase() < b.title.toLowerCase() ? -1 : 1;
        });
        render();
      });
    }).catch(fail);
  }

  // ---- sign in ---------------------------------------------------

  function renderLogin() {
    var box = el('div', 'login up');
    var logo = el('img');
    logo.src = '/ui/logo-light.png'; logo.width = 44; logo.height = 44; logo.alt = '';
    box.appendChild(logo);
    box.appendChild(el('h2', '', 'Enter the code on the phone'));
    box.appendChild(el('span', 'sub', 'On the phone, open Files to see its six-digit access code.'));
    var code = input('text', '000 000');
    code.className = 'codein';
    code.maxLength = 7;
    code.inputMode = 'numeric';
    box.appendChild(code);
    var go = button('primary', 'Open', submit);
    box.appendChild(go);
    var msg = el('span', 'msg', ui.note);
    box.appendChild(msg);
    code.addEventListener('keydown', function (e) { if (e.key === 'Enter') { submit(); } });
    function submit() {
      api('POST', '/api/login', { code: code.value }).then(function () {
        say(''); return load();
      }).catch(function (err) { msg.textContent = err.message; });
    }
    app.appendChild(box);
    code.focus();
  }

  // ---- frame -----------------------------------------------------

  function renderNav() {
    var nav = el('nav');
    nav.setAttribute('aria-label', 'Menu');
    st.services.forEach(function (s) {
      nav.appendChild(button('tab' + (ui.tab === s.id ? ' on' : ''), TABS[s.id], function () {
        ui.tab = s.id; ui.open = null; ui.confirm = null; enter();
      }));
    });
    var foot = el('div', 'navfoot');
    var link = el('div', 'link');
    var logo = el('img');
    logo.src = '/ui/logo-light.png'; logo.width = 30; logo.height = 30; logo.alt = '';
    link.appendChild(logo);
    link.appendChild(el('span', '', '● Connected by cable'));
    foot.appendChild(link);
    foot.appendChild(button('ghost', 'Sign out', function () {
      api('POST', '/api/logout').then(function () { st = null; ui.entries = null; ui.chat = []; render(); });
    }));
    nav.appendChild(foot);
    app.appendChild(nav);
  }
  function heading(main, title, sub, side) {
    var row = el('div', 'headrow up');
    var left = el('div');
    left.appendChild(el('h2', '', title));
    left.appendChild(el('span', 'sub', sub));
    row.appendChild(left);
    if (side) { row.appendChild(typeof side === 'string' ? el('span', 'side', side) : side); }
    main.appendChild(row);
  }
  function stopped(main, title) {
    heading(main, title, 'This service is stopped.');
    var p = el('div', 'panel warn narrow');
    p.appendChild(el('b', '', 'Stopped on the phone'));
    p.appendChild(el('p', '', 'Open it on the phone’s screen and tap Start, then come back here.'));
    p.appendChild(button('mini', 'Check again', load));
    main.appendChild(p);
  }

  // ---- files and media -------------------------------------------

  function send(list) {
    var queue = Array.prototype.slice.call(list);
    if (!queue.length || ui.upload) { return; }
    say('');
    next();
    function next() {
      var file = queue.shift();
      if (!file) { ui.upload = null; ui.done = true; loadFiles(); load(); return; }
      ui.upload = { name: file.name, part: 0 };
      render();
      var xhr = new XMLHttpRequest();
      xhr.open('PUT', '/api/files/' + encodeURIComponent(file.name));
      xhr.setRequestHeader('X-Hub', '1');
      xhr.upload.addEventListener('progress', function (e) {
        if (e.lengthComputable && ui.upload) {
          ui.upload.part = e.loaded / e.total;
          var fill = document.getElementById('upfill');
          if (fill) { fill.style.width = Math.round(ui.upload.part * 100) + '%'; }
        }
      });
      xhr.addEventListener('load', function () {
        if (xhr.status === 200) { ui.fresh[file.name] = true; next(); return; }
        var text = 'Could not send ' + file.name + '.';
        try { text = JSON.parse(xhr.responseText).error || text; } catch (ignored) { /* keep */ }
        ui.upload = null; say(text); loadFiles();
      });
      xhr.addEventListener('error', function () {
        ui.upload = null; say('Lost the phone while sending ' + file.name + '.'); render();
      });
      xhr.send(file);
    }
  }
  function renderFiles(main) {
    heading(main, 'Files and media',
      'Stored on the phone. Reachable from this computer through the cable.',
      gb(st.storage.free) + ' free of ' + gb(st.storage.total));
    var drop = button('drop up' + (ui.upload ? ' busy' : ''), null, function () { picker.click(); });
    drop.appendChild(icon('upload'));
    drop.appendChild(el('b', '', ui.upload ? 'Sending ' + ui.upload.name + ' to the phone…'
      : ui.done ? 'Sent. Drop more files here' : 'Drop files here to keep them on the phone'));
    drop.appendChild(el('span', '', 'or click to choose files'));
    if (ui.upload) {
      var bar = el('span', 'bar');
      var fill = el('span');
      fill.id = 'upfill';
      fill.style.width = Math.round(ui.upload.part * 100) + '%';
      bar.appendChild(fill);
      drop.appendChild(bar);
    }
    drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', function () { drop.classList.remove('over'); });
    drop.addEventListener('drop', function (e) {
      e.preventDefault(); drop.classList.remove('over');
      if (e.dataTransfer) { send(e.dataTransfer.files); }
    });
    main.appendChild(drop);
    if (ui.note) { main.appendChild(el('span', 'msg', ui.note)); }

    if (ui.open) {
      var v = el('div', 'viewer up');
      var vh = el('div', 'vhead');
      vh.appendChild(el('span', '', ui.open.name));
      vh.appendChild(button('mini', 'Close', function () { ui.open = null; render(); }));
      v.appendChild(vh);
      var src = '/api/files/' + encodeURIComponent(ui.open.name);
      var media;
      if (ui.open.kind === 'photo') {
        media = el('img'); media.alt = ui.open.name;
      } else {
        media = el(ui.open.kind === 'music' ? 'audio' : 'video');
        media.controls = true; media.autoplay = true;
      }
      media.src = src;
      v.appendChild(media);
      main.appendChild(v);
    }

    var chips = el('div', 'chips');
    [['all', 'All'], ['photo', 'Photos'], ['music', 'Music'], ['video', 'Video'], ['file', 'Other']]
      .forEach(function (c) {
        chips.appendChild(button('fchip' + (ui.filter === c[0] ? ' on' : ''), c[1], function () {
          ui.filter = c[0]; render();
        }));
      });
    main.appendChild(chips);

    var list = el('div', 'list');
    var shown = ui.files.filter(function (f) { return ui.filter === 'all' || f.kind === ui.filter; });
    if (!shown.length) {
      list.appendChild(el('div', 'empty', ui.files.length ? 'Nothing of this kind yet.' : 'Nothing on the phone yet.'));
    }
    shown.forEach(function (f) {
      var item = el('div', 'item' + (ui.fresh[f.name] ? ' up' : ''));
      item.appendChild(icon(f.kind));
      item.appendChild(el('span', 'name', f.name));
      if (ui.fresh[f.name]) { item.appendChild(el('span', 'tag', 'Just now')); }
      item.appendChild(el('span', 'size', size(f.bytes)));
      if (f.kind !== 'file') {
        item.appendChild(button('mini', f.kind === 'photo' ? 'View' : 'Play', function () {
          ui.open = f; render(); window.scrollTo(0, 0);
        }));
      }
      var get = el('a', 'mini', 'Download');
      get.href = '/api/files/' + encodeURIComponent(f.name) + '?download=1';
      get.setAttribute('download', f.name);
      item.appendChild(get);
      var sure = ui.confirm === f.name;
      item.appendChild(button('mini danger', sure ? 'Really delete?' : 'Delete', function () {
        if (!sure) { ui.confirm = f.name; render(); return; }
        ui.confirm = null;
        if (ui.open && ui.open.name === f.name) { ui.open = null; }
        api('DELETE', '/api/files/' + encodeURIComponent(f.name)).then(function () {
          loadFiles(); load();
        }).catch(fail);
      }));
      list.appendChild(item);
    });
    main.appendChild(list);
  }

  // ---- assistant -------------------------------------------------

  function renderAssistant(main) {
    var a = st.assistant;
    var local = a.mode === 'local';
    var seg = el('div', 'seg');
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', 'Model');
    var bl = button(local ? 'on' : '', 'On the phone', function () { mode('local'); });
    if (!a.local_name) { bl.disabled = true; bl.title = 'This phone is too small to run a model itself.'; }
    seg.appendChild(bl);
    seg.appendChild(button(local ? '' : 'on', 'Cloud', function () { mode('cloud'); }));
    heading(main, 'Assistant', local
      ? a.local_name + ', running on the phone. Works even with the phone network off.'
      : a.cloud_name + '. Needs internet and your own key.', seg);

    if (!local && !a.key_set) {
      var p = el('div', 'panel narrow up');
      p.appendChild(el('b', '', 'Add your cloud key'));
      p.appendChild(el('p', '', 'It is saved on the phone, locked to the phone, and never shown again. Remove it any time from the phone’s screen.'));
      var key = input('password', 'Paste your key');
      p.appendChild(key);
      var row = el('div', 'btnrow');
      row.appendChild(button('primary', 'Save key', function () {
        api('POST', '/api/assistant/key', { key: key.value }).then(function (next) {
          st = next; say(''); render();
        }).catch(fail);
      }));
      row.appendChild(el('span', 'msg', ui.note));
      p.appendChild(row);
      main.appendChild(p);
      return;
    }

    var chat = el('div', 'chat');
    if (!ui.chat.length && !ui.thinking) {
      chat.appendChild(el('span', 'chathint', local
        ? 'Ask anything. The answer is worked out on the phone itself, so nothing leaves your home.'
        : 'Ask anything. The question goes to your cloud provider using your own key.'));
    }
    ui.chat.forEach(function (m) {
      if (m.q) { chat.appendChild(el('span', 'q', m.q)); return; }
      var box = el('div', 'a up' + (m.err ? ' err' : ''));
      box.appendChild(el('span', 'text', m.a));
      if (m.stats) { box.appendChild(el('span', 'stats', m.stats)); }
      chat.appendChild(box);
    });
    if (ui.thinking) {
      var dots = el('span', 'dots');
      dots.appendChild(el('span', '', '•')); dots.appendChild(el('span', '', '•')); dots.appendChild(el('span', '', '•'));
      chat.appendChild(dots);
    }
    main.appendChild(chat);
    var row2 = el('div', 'askrow');
    var box2 = el('textarea');
    box2.rows = 1; box2.placeholder = 'Type your question'; box2.value = ui.draft;
    box2.addEventListener('input', function () { ui.draft = box2.value; });
    box2.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); }
    });
    row2.appendChild(box2);
    var go = button('primary', 'Ask', ask);
    if (ui.thinking) { go.disabled = true; }
    row2.appendChild(go);
    main.appendChild(row2);
    chat.scrollTop = chat.scrollHeight;
    if (!ui.thinking) { box2.focus(); }

    function ask() {
      var q = ui.draft.trim();
      if (!q || ui.thinking) { return; }
      ui.chat.push({ q: q }); ui.draft = ''; ui.thinking = true; render();
      api('POST', '/api/assistant/ask', { question: q }).then(function (r) {
        var where = r.mode === 'local' ? 'On the phone · offline' : r.name;
        ui.chat.push({ a: r.answer, stats: where + ' · ' + (r.ms / 1000).toFixed(1) + ' s' });
      }).catch(function (err) {
        ui.chat.push({ a: err.message, err: true });
      }).then(function () { ui.thinking = false; render(); });
    }
    function mode(m) {
      api('POST', '/api/assistant/mode', { mode: m }).then(function (next) {
        st = next; say(''); render();
      }).catch(fail);
    }
  }

  // ---- password vault --------------------------------------------

  function generate() {
    var set = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_=+';
    var raw = new Uint32Array(20);
    window.crypto.getRandomValues(raw);
    var out = '';
    for (var i = 0; i < raw.length; i++) { out += set.charAt(raw[i] % set.length); }
    return out;
  }
  function renderVault(main) {
    var v = st.vault;
    if (!v.exists) {
      heading(main, 'Passwords', 'Kept on the phone, sealed with one master password.');
      var p = el('div', 'panel narrow up');
      p.appendChild(el('b', '', 'Choose a master password'));
      p.appendChild(el('p', '', 'It is the only way in. It is never stored, so nobody can reset it for you: if you forget it, the passwords are gone. At least 8 characters; a few random words work well.'));
      var a = input('password', 'Master password');
      var b = input('password', 'Type it again');
      p.appendChild(a); p.appendChild(b);
      var row = el('div', 'btnrow');
      row.appendChild(button('primary', 'Create vault', function () {
        if (a.value !== b.value) { say('The two passwords are not the same.'); render(); return; }
        api('POST', '/api/vault/create', { password: a.value }).then(function () {
          say(''); return loadVault();
        }).catch(fail);
      }));
      row.appendChild(el('span', 'msg', ui.note));
      p.appendChild(row);
      main.appendChild(p);
      return;
    }
    if (!v.unlocked || !ui.entries) {
      heading(main, 'Passwords', 'Locked. Kept on the phone, sealed with your master password.');
      var u = el('div', 'panel narrow up');
      u.appendChild(el('b', '', 'Unlock the vault'));
      var pw = input('password', 'Master password');
      u.appendChild(pw);
      var urow = el('div', 'btnrow');
      urow.appendChild(button('primary', 'Unlock', unlock));
      urow.appendChild(el('span', 'msg', ui.note));
      u.appendChild(urow);
      pw.addEventListener('keydown', function (e) { if (e.key === 'Enter') { unlock(); } });
      main.appendChild(u);
      pw.focus();
      return;
    }
    var lock = button('ghost', 'Lock now', function () {
      api('POST', '/api/vault/lock').then(function () { ui.pick = null; return loadVault(); });
    });
    heading(main, 'Passwords', ui.entries.length + (ui.entries.length === 1 ? ' entry' : ' entries')
      + '. Locks itself after five quiet minutes.', lock);
    var tools = el('div', 'btnrow');
    var search = input('search', 'Search', ui.search);
    search.style.maxWidth = '320px';
    search.addEventListener('input', function () {
      ui.search = search.value; render();
      var again = document.getElementById('vsearch');
      if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
    });
    search.id = 'vsearch';
    tools.appendChild(search);
    tools.appendChild(button('mini', 'Add entry', function () {
      ui.pick = { title: '', username: '', password: '', url: '', notes: '' };
      ui.show = true; say(''); render();
    }));
    tools.appendChild(button('mini', 'Change master password', function () {
      ui.pick = { change: true }; say(''); render();
    }));
    main.appendChild(tools);

    var split = el('div', 'split');
    var list = el('div', 'list');
    var needle = ui.search.toLowerCase();
    var shown = ui.entries.filter(function (e) {
      return !needle || (e.title + ' ' + e.username + ' ' + e.url).toLowerCase().indexOf(needle) >= 0;
    });
    if (!shown.length) {
      list.appendChild(el('div', 'empty', ui.entries.length ? 'No entry matches.' : 'No passwords yet. Add your first entry.'));
    }
    shown.forEach(function (e) {
      var item = el('div', 'item pick' + (ui.pick && ui.pick.id === e.id ? ' sel' : ''));
      item.appendChild(icon('key'));
      item.appendChild(el('span', 'name', e.title));
      item.appendChild(el('span', 'who', e.username));
      item.addEventListener('click', function () {
        ui.pick = { id: e.id, title: e.title, username: e.username, password: e.password, url: e.url, notes: e.notes };
        ui.show = false; ui.confirm = null; say(''); render();
      });
      list.appendChild(item);
    });
    split.appendChild(list);
    if (ui.pick && ui.pick.change) { split.appendChild(changePanel()); }
    else if (ui.pick) { split.appendChild(editor()); }
    main.appendChild(split);

    function unlock() {
      api('POST', '/api/vault/unlock', { password: pw.value }).then(function () {
        say(''); return loadVault();
      }).catch(fail);
    }
  }
  function changePanel() {
    var p = el('div', 'panel up');
    p.appendChild(el('b', '', 'Change master password'));
    var cur = input('password', 'Current master password');
    var a = input('password', 'New master password');
    var b = input('password', 'Type it again');
    p.appendChild(cur); p.appendChild(a); p.appendChild(b);
    var row = el('div', 'btnrow');
    row.appendChild(button('primary', 'Change', function () {
      if (a.value !== b.value) { say('The two passwords are not the same.'); render(); return; }
      api('POST', '/api/vault/password', { current: cur.value, next: a.value }).then(function () {
        ui.pick = null; say('Master password changed.', true); render();
      }).catch(fail);
    }));
    row.appendChild(button('ghost', 'Cancel', function () { ui.pick = null; say(''); render(); }));
    p.appendChild(row);
    p.appendChild(el('span', 'msg', ui.note));
    return p;
  }
  function editor() {
    var e = ui.pick;
    var p = el('div', 'panel up');
    p.appendChild(el('b', '', e.id ? 'Entry' : 'New entry'));
    var title = input('text', 'For example: Bank', e.title);
    var user = input('text', '', e.username);
    var pass = input(ui.show ? 'text' : 'password', '', e.password);
    pass.className = 'mono';
    var url = input('text', 'https://', e.url);
    var notes = el('textarea');
    notes.rows = 3; notes.value = e.notes;
    function keep() {
      e.title = title.value; e.username = user.value; e.password = pass.value;
      e.url = url.value; e.notes = notes.value;
    }
    function copy(text, what) {
      keep();
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(function () { say(what + ' copied.', true); render(); },
          function () { say('This browser did not allow copying.'); render(); });
      } else { say('This browser did not allow copying.'); render(); }
    }
    p.appendChild(field('Name', title));
    var urow = el('div', 'fieldrow');
    urow.appendChild(user);
    urow.appendChild(button('mini', 'Copy', function () { copy(user.value, 'User name'); }));
    p.appendChild(field('User name', urow));
    var prow = el('div', 'fieldrow');
    prow.appendChild(pass);
    prow.appendChild(button('mini', ui.show ? 'Hide' : 'Show', function () { keep(); ui.show = !ui.show; render(); }));
    prow.appendChild(button('mini', 'Copy', function () { copy(pass.value, 'Password'); }));
    prow.appendChild(button('mini', 'Make one', function () {
      keep(); e.password = generate(); ui.show = true; render();
    }));
    p.appendChild(field('Password', prow));
    p.appendChild(field('Web address', url));
    p.appendChild(field('Notes', notes));
    var row = el('div', 'btnrow');
    row.appendChild(button('primary', 'Save', function () {
      keep();
      api('POST', '/api/vault/entries', e).then(function (r) {
        e.id = r.id; say('Saved.', true);
        return api('GET', '/api/vault/entries').then(function (data) {
          ui.entries = data.entries.sort(function (x, y) {
            return x.title.toLowerCase() < y.title.toLowerCase() ? -1 : 1;
          });
          render();
        });
      }).catch(fail);
    }));
    row.appendChild(button('ghost', 'Close', function () { ui.pick = null; say(''); render(); }));
    if (e.id) {
      var sure = ui.confirm === e.id;
      row.appendChild(button('mini danger', sure ? 'Really delete?' : 'Delete', function () {
        if (!sure) { keep(); ui.confirm = e.id; render(); return; }
        ui.confirm = null;
        api('DELETE', '/api/vault/entries/' + encodeURIComponent(e.id)).then(function () {
          ui.pick = null; say(''); return loadVault();
        }).catch(fail);
      }));
    }
    p.appendChild(row);
    p.appendChild(el('span', 'msg' + (ui.noteOk ? ' ok' : ''), ui.note));
    return p;
  }

  // ---- draw ------------------------------------------------------

  function render() {
    while (app.firstChild) { app.removeChild(app.firstChild); }
    if (!st) { renderLogin(); return; }
    renderNav();
    var main = el('main');
    app.appendChild(main);
    var svc = service(ui.tab);
    if (!svc) {
      heading(main, 'Nothing here', 'This phone was built without services.');
    } else if (!svc.running) {
      stopped(main, TABS[svc.id]);
    } else if (svc.id === 'files') {
      renderFiles(main);
    } else if (svc.id === 'assistant') {
      renderAssistant(main);
    } else {
      renderVault(main);
    }
  }

  picker.addEventListener('change', function () { send(picker.files); picker.value = ''; });
  window.addEventListener('dragover', function (e) { e.preventDefault(); });
  window.addEventListener('drop', function (e) { e.preventDefault(); });
  load();
}());
