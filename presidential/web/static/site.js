/* Static build only: the exported site has no server, so the filter forms and the timeline
   indicator picker work in the browser. Rows carry their filter keys as data-* attributes. */
(function () {
  var qs = new URLSearchParams(location.search);
  var page = document.body.getAttribute('data-page') || '';

  /* ---- timeline: every series is rendered; show the chosen ones ---- */
  var tl = document.getElementById('tlform');
  if (tl) {
    var panels = [].slice.call(document.querySelectorAll('.panel[data-ind]'));
    var boxes = [].slice.call(tl.querySelectorAll('input[name=ind]'));
    var draw = tl.querySelector('button');
    if (draw) draw.removeAttribute('onclick');
    var show = function (list) {
      panels.forEach(function (p) { p.style.display = list.indexOf(p.getAttribute('data-ind')) >= 0 ? '' : 'none'; });
      boxes.forEach(function (b) { b.checked = list.indexOf(b.value) >= 0; });
    };
    var chosen = function () { return boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }); };
    if (window.TL_CANONICAL) {
      var have = panels.map(function (p) { return p.getAttribute('data-ind'); });
      var want = (qs.get('indicators') || tl.getAttribute('data-default') || '').split(',')
        .filter(function (i) { return have.indexOf(i) >= 0; });
      if (want.length) show(want);
      [].forEach.call(tl.querySelectorAll('input[name=start],input[name=end]'), function (i) {
        i.disabled = true;
        i.title = 'The published build covers one date range. Run the project locally (./pres serve) for other ranges.';
      });
    }
    tl.addEventListener('change', function (e) { if (e.target.name === 'ind') show(chosen()); });
    tl.addEventListener('submit', function (e) {
      e.preventDefault();
      var list = chosen();
      show(list);
      history.replaceState(null, '', location.pathname + (list.length ? '?indicators=' + list.join(',') : ''));
    });
  }

  /* ---- listings: filter rows against the query string ---- */
  var rows = [].slice.call(document.querySelectorAll('[data-row]'));
  if (!rows.length) return;
  var active = [];
  qs.forEach(function (v, k) { if (v) active.push([k, v]); });

  active.forEach(function (kv) {                       // reflect the query in the form controls
    var el = document.querySelector('form.filters [name="' + kv[0] + '"]');
    if (el) el.value = kv[1];
  });

  var hit = function (el, k, v) {
    if (k === 'text') return (el.getAttribute('data-text') || '').indexOf(v.toLowerCase()) >= 0;
    var a = el.getAttribute('data-' + k);
    if (a === null) return true;                        // filter this page does not carry: ignore it
    return (' ' + a + ' ').indexOf(' ' + v + ' ') >= 0;
  };

  var n = 0;
  rows.forEach(function (r) {
    var ok = active.every(function (kv) { return hit(r, kv[0], kv[1]); });
    r.style.display = ok ? '' : 'none';
    if (ok) n++;
  });

  var count = document.querySelector('h1 .muted');
  if (count && active.length) count.textContent = '(' + n + ' of ' + rows.length + ')';

  if (page === 'documents' && qs.get('text')) {         // FTS5 over the archived text needs the local app
    var f = document.querySelector('form.filters');
    if (f) {
      var p = document.createElement('p');
      p.className = 'small muted';
      p.textContent = 'Published build: this searches titles, authors and dates. Full-text search inside the archived documents runs in the local app (./pres serve).';
      f.parentNode.insertBefore(p, f.nextSibling);
    }
  }
})();
