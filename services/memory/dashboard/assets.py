"""Self-contained (offline) dashboard markup, styles, and script.

Kept as one triple-quoted string so server.py serves it inline with no
external CDN dependency. Split out of server.py to keep each file focused.
"""

PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shared Brain · Control Panel</title>
<style>
:root {
  color-scheme: dark;
  --bg:            oklch(16% 0.012 265);
  --bg-elev:       oklch(20% 0.016 265);
  --bg-elev-2:     oklch(24% 0.018 265);
  --line:          oklch(30% 0.02 265);
  --line-soft:     oklch(27% 0.016 265);
  --text:          oklch(94% 0.008 265);
  --text-dim:      oklch(72% 0.014 265);
  --text-faint:    oklch(58% 0.016 265);
  --accent:        oklch(72% 0.16 200);
  --accent-2:      oklch(70% 0.19 300);
  --brain:         oklch(76% 0.17 90);
  --ok:            oklch(74% 0.17 155);
  --warn:          oklch(80% 0.15 80);
  --bad:           oklch(66% 0.20 25);
  --radius:        14px;
  --radius-sm:     9px;
  --space:         clamp(1rem, 0.6rem + 1.4vw, 1.75rem);
  --dur:           220ms;
  --ease:          cubic-bezier(0.16, 1, 0.3, 1);
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
  --sans: system-ui, -apple-system, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body {
  background:
    radial-gradient(1200px 600px at 85% -10%, oklch(24% 0.05 300 / 0.35), transparent 60%),
    radial-gradient(1000px 500px at -5% 0%, oklch(24% 0.05 200 / 0.30), transparent 55%),
    var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  display: grid;
  grid-template-columns: 250px 1fr;
  min-height: 100%;
}
a { color: var(--accent); }

/* ---- Sidebar nav ---- */
.sidebar {
  border-right: 1px solid var(--line);
  padding: 1.4rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  position: sticky;
  top: 0;
  height: 100vh;
  background: oklch(15% 0.012 265 / 0.6);
  backdrop-filter: blur(6px);
}
.brand {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0.2rem 0.5rem 1.2rem;
}
.brand .glyph { font-size: 1.5rem; filter: drop-shadow(0 0 10px oklch(76% 0.17 90 / 0.6)); }
.brand h1 {
  font-size: 0.98rem; margin: 0; letter-spacing: 0.02em; font-weight: 650;
}
.brand small { display: block; color: var(--text-faint); font-size: 0.68rem;
  letter-spacing: 0.14em; text-transform: uppercase; font-weight: 600; }
.nav-btn {
  appearance: none; border: 1px solid transparent; background: transparent;
  color: var(--text-dim); text-align: left; font: inherit; font-weight: 550;
  padding: 0.6rem 0.75rem; border-radius: var(--radius-sm); cursor: pointer;
  display: flex; align-items: center; gap: 0.65rem;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease),
              transform var(--dur) var(--ease);
}
.nav-btn .ico { width: 1.1rem; text-align: center; opacity: 0.9; }
.nav-btn:hover { background: var(--bg-elev); color: var(--text); transform: translateX(2px); }
.nav-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.nav-btn[aria-current="true"] {
  background: linear-gradient(90deg, oklch(72% 0.16 200 / 0.18), transparent);
  border-color: var(--line);
  color: var(--text);
  box-shadow: inset 3px 0 0 var(--accent);
}
.nav-spacer { flex: 1; }
.nav-foot { color: var(--text-faint); font-size: 0.72rem; padding: 0.5rem; }
.pulse { display:inline-block; width:8px; height:8px; border-radius:50%;
  background: var(--ok); box-shadow: 0 0 8px var(--ok); margin-right:6px;
  animation: pulse 2.4s var(--ease) infinite; }
@keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:0.35;} }

/* ---- Main ---- */
main { padding: var(--space) clamp(1rem, 0.5rem + 2vw, 2.5rem); min-width: 0; }
.view { display: none; animation: fade var(--dur) var(--ease); }
.view.active { display: block; }
@keyframes fade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
.view-head { margin: 0 0 1.4rem; }
.view-head h2 { margin: 0; font-size: clamp(1.4rem, 1rem + 1.4vw, 2rem); letter-spacing: -0.01em; }
.view-head p { margin: 0.25rem 0 0; color: var(--text-dim); }

/* ---- Tiles ---- */
.tiles { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); }
.tile {
  background: linear-gradient(160deg, var(--bg-elev), var(--bg-elev-2));
  border: 1px solid var(--line); border-radius: var(--radius);
  padding: 1.1rem 1.2rem; position: relative; overflow: hidden;
  transition: transform var(--dur) var(--ease), border-color var(--dur) var(--ease);
}
.tile:hover { transform: translateY(-3px); border-color: var(--line-soft); }
.tile::after {
  content: ""; position: absolute; inset: 0 0 auto 0; height: 3px;
  background: var(--c, var(--accent)); opacity: 0.85;
}
.tile .label { color: var(--text-faint); font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.12em; font-weight: 650; display:flex; align-items:center; gap:0.4rem; }
.tile .value { font-size: 2rem; font-weight: 680; margin: 0.35rem 0 0.1rem; font-variant-numeric: tabular-nums; }
.tile .sub { color: var(--text-dim); font-size: 0.82rem; font-family: var(--mono); }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.dot.ok { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
.dot.warn { background: var(--warn); box-shadow: 0 0 8px var(--warn); }
.dot.bad { background: var(--bad); box-shadow: 0 0 8px var(--bad); }
.st-ok { --c: var(--ok); } .st-warn { --c: var(--warn); } .st-bad { --c: var(--bad); }

/* ---- Overview detail ---- */
.overview-grid {
  display: grid; gap: 1rem; margin-top: 1rem;
  grid-template-columns: minmax(300px, 1.2fr) minmax(280px, 0.8fr);
}
.overview-panel {
  background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 1rem; min-width: 0;
}
.overview-panel h3 {
  margin: 0 0 0.8rem; font-size: 0.86rem; letter-spacing: 0.11em;
  text-transform: uppercase; color: var(--text-dim);
}
.panel-row {
  display: grid; grid-template-columns: minmax(110px, 0.35fr) minmax(0, 1fr);
  gap: 0.65rem; padding: 0.48rem 0; border-top: 1px solid var(--line-soft);
}
.panel-row:first-of-type { border-top: 0; padding-top: 0; }
.panel-row .k { color: var(--text-faint); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; }
.panel-row .v { color: var(--text); font-family: var(--mono); overflow-wrap: anywhere; }
.scope-bars { display: grid; gap: 0.65rem; }
.scope-line { display: grid; grid-template-columns: minmax(90px, 160px) 1fr 42px; align-items: center; gap: 0.65rem; }
.scope-name { font-family: var(--mono); color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar { height: 8px; border-radius: 99px; background: var(--bg-elev-2); border: 1px solid var(--line-soft); overflow: hidden; }
.bar > span { display: block; height: 100%; width: var(--w, 0%); background: linear-gradient(90deg, var(--brain), var(--accent)); }
.scope-num { color: var(--text-faint); font-family: var(--mono); text-align: right; }
.mini-list { display: grid; gap: 0.55rem; }
.mini-item {
  border: 1px solid var(--line-soft); border-radius: var(--radius-sm);
  padding: 0.65rem 0.75rem; background: oklch(18% 0.014 265);
  cursor: pointer; transition: border-color var(--dur) var(--ease), transform var(--dur) var(--ease);
}
.mini-item:hover { border-color: var(--accent); transform: translateY(-1px); }
.mini-item strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini-item span { color: var(--text-faint); font: 0.75rem var(--mono); }
.quick-actions { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.65rem; margin-top: 1rem; }
.quick-actions .btn { text-align: center; }
.wide { grid-column: 1 / -1; }

/* ---- Controls ---- */
.toolbar { display: flex; flex-wrap: wrap; gap: 0.7rem; margin-bottom: 1.2rem; align-items: center; }
input[type=text], input[type=search], select, textarea {
  background: var(--bg-elev); border: 1px solid var(--line); color: var(--text);
  border-radius: var(--radius-sm); padding: 0.6rem 0.8rem; font: inherit;
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
input:focus, select:focus, textarea:focus {
  outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 3px oklch(72% 0.16 200 / 0.22);
}
.search-box { flex: 1 1 260px; min-width: 200px; }
select { cursor: pointer; }
.btn {
  appearance: none; border: 1px solid var(--line); border-radius: var(--radius-sm);
  background: var(--bg-elev-2); color: var(--text); font: inherit; font-weight: 600;
  padding: 0.6rem 1rem; cursor: pointer;
  transition: transform var(--dur) var(--ease), background var(--dur) var(--ease), border-color var(--dur) var(--ease);
}
.btn:hover { transform: translateY(-1px); border-color: var(--accent); }
.btn:active { transform: translateY(0); }
.btn.primary { background: linear-gradient(180deg, var(--accent), oklch(64% 0.16 200));
  color: oklch(18% 0.03 200); border-color: transparent; }
.btn.ghost { background: transparent; }
/* Irreversible actions read as irreversible before they are clicked, not after. */
.btn.danger { color: var(--bad); }
.btn.danger:hover { border-color: var(--bad); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* ---- Memory cards ---- */
.mem-group-title { display:flex; align-items:center; gap:0.5rem; margin: 1.6rem 0 0.8rem;
  font-size: 0.78rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-faint); font-weight: 700; }
.mem-group-title .count { color: var(--text-faint); font-family: var(--mono); }
.cards { display: grid; gap: 0.85rem; }
.card {
  background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 1rem 1.1rem; position: relative;
  transition: border-color var(--dur) var(--ease), transform var(--dur) var(--ease);
  cursor: pointer;
}
.card:hover { border-color: var(--line-soft); transform: translateY(-2px); }
.card.shared { border-color: oklch(76% 0.17 90 / 0.4);
  background: linear-gradient(160deg, oklch(22% 0.03 90 / 0.35), var(--bg-elev)); }
.card .text { margin: 0 0 0.7rem; padding-right: 2rem; }
.card .meta { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; }
.badge { font-size: 0.72rem; font-family: var(--mono); padding: 0.18rem 0.55rem;
  border-radius: 999px; border: 1px solid var(--line); color: var(--text-dim);
  display:inline-flex; align-items:center; gap:0.3rem; }
.badge.scope { border-color: oklch(70% 0.19 300 / 0.5); color: oklch(80% 0.12 300); }
.badge.scope.shared { border-color: var(--brain); color: var(--brain); }
.badge.agent { border-color: oklch(72% 0.16 200 / 0.5); color: oklch(82% 0.10 200); }
.badge.time { border-color: transparent; color: var(--text-faint); }
.badge.score { border-color: oklch(74% 0.17 155 / 0.5); color: var(--ok); }
.del {
  position: absolute; top: 0.7rem; right: 0.7rem; width: 28px; height: 28px;
  border-radius: 8px; border: 1px solid var(--line); background: transparent;
  color: var(--text-faint); cursor: pointer; font-size: 0.95rem; line-height: 1;
  transition: color var(--dur) var(--ease), border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.del:hover { color: var(--bad); border-color: var(--bad); background: oklch(66% 0.2 25 / 0.12); }
.del:focus-visible { outline: 2px solid var(--bad); outline-offset: 2px; }

textarea { width: 100%; resize: vertical; min-height: 70px; font-family: var(--sans); }
.add-memory-grid { display: grid; gap: 0.75rem; }
.add-memory-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }

/* ---- Generic list (skills/agents/rules/mcp) ---- */
.list { display: grid; gap: 0.7rem; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
.item {
  background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius-sm);
  padding: 0.85rem 1rem; transition: border-color var(--dur) var(--ease), transform var(--dur) var(--ease);
  cursor: pointer;
}
.item:hover { border-color: var(--line-soft); transform: translateY(-2px); }
.item .name { font-weight: 640; display:flex; align-items:center; gap:0.5rem; }
.item .name .src { font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.1em;
  color: var(--text-faint); border: 1px solid var(--line); padding: 0.05rem 0.35rem; border-radius: 5px; }
.item .desc { color: var(--text-dim); font-size: 0.86rem; margin-top: 0.3rem;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.rule-group { margin-bottom: 1.2rem; }
.rule-group h3 { margin: 0 0 0.5rem; font-size: 0.82rem; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--accent); }
.rule-files { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.chip { font-family: var(--mono); font-size: 0.78rem; background: var(--bg-elev);
  border: 1px solid var(--line); border-radius: 7px; padding: 0.3rem 0.6rem; color: var(--text-dim);
  cursor: pointer; transition: border-color var(--dur) var(--ease), color var(--dur) var(--ease); }
.chip:hover { border-color: var(--accent); color: var(--text); }
.chip { display: inline-flex; align-items: center; gap: 0.35rem; }
.hd-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
.add-btn { flex: none; white-space: nowrap; }
.item { position: relative; }
.del-x { position: absolute; top: 0.5rem; right: 0.5rem; width: 1.5rem; height: 1.5rem;
  display: grid; place-items: center; border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg-elev); color: var(--text-faint); font-size: 0.8rem; cursor: pointer;
  opacity: 0; transition: opacity var(--dur) var(--ease), color var(--dur) var(--ease),
  border-color var(--dur) var(--ease); }
.item:hover .del-x, .chip:hover .del-x { opacity: 1; }
.del-x:hover { color: #ff6b6b; border-color: #ff6b6b; }
.chip .del-x { position: static; width: 1rem; height: 1rem; border: 0; background: none; opacity: 0.5; }
.chip:hover .del-x { opacity: 1; }
.form-grid { display: grid; gap: 0.6rem; margin-top: 0.6rem; }
.form-grid input, .form-grid select { width: 100%; }
/* ---- Projects ---- */
.proj-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 0.9rem; }
.proj { background: var(--bg-elev); border: 1px solid var(--line); border-radius: 12px;
  padding: 0.9rem 1rem; cursor: pointer; display: flex; flex-direction: column; gap: 0.5rem;
  transition: border-color var(--dur) var(--ease), transform var(--dur) var(--ease); }
.proj:hover { border-color: var(--accent); transform: translateY(-2px); }
.proj-top { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
.proj-name { font-weight: 600; font-size: 0.98rem; color: var(--text); }
.pbadges { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.pbadge { font-size: 0.68rem; font-family: var(--mono); padding: 0.12rem 0.45rem; border-radius: 999px;
  border: 1px solid var(--line); color: var(--text-dim); white-space: nowrap; }
.pbadge.clean { color: #4ade80; border-color: #2c6e49; }
.pbadge.dirty { color: #fbbf24; border-color: #7a5c15; }
.pbadge.push { color: #60a5fa; border-color: #2b5486; }
.pbadge.behind { color: #f472b6; border-color: #7a3560; }
.pbadge.nogit { color: var(--text-faint); }
.proj-commit { font-size: 0.82rem; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.phash { font-family: var(--mono); font-size: 0.76rem; color: var(--accent); }
.proj-foot { display: flex; align-items: center; gap: 0.7rem; font-size: 0.76rem; color: var(--text-faint); flex-wrap: wrap; }
/* ---- Code Map styles ---- */
.hd-actions { display: flex; gap: 0.5rem; align-items: center; }
.cm-stats { display: flex; gap: 1.4rem; flex-wrap: wrap; align-items: baseline; margin-bottom: 1rem; }
.cm-stat { font-variant-numeric: tabular-nums; color: var(--text-faint); font-size: 0.85rem; }
.cm-stat b { font-size: 1.3rem; color: var(--text); margin-right: 0.25rem; }
.cm-badge { padding: 0.15rem 0.55rem; border-radius: 999px; font-size: 0.78rem; border: 1px solid var(--line); }
.cm-badge.fresh { color: var(--ok); border-color: color-mix(in oklch, var(--ok) 40%, transparent); }
.cm-badge.stale { color: var(--warn); border-color: color-mix(in oklch, var(--warn) 40%, transparent); }
.cm-badge.diverged { color: var(--bad); border-color: color-mix(in oklch, var(--bad) 40%, transparent); }
.cm-badge.unknown, .cm-badge.unknown_commit { color: var(--text-faint); }
.cm-warn { padding: 0.6rem 0.8rem; border: 1px solid color-mix(in oklch, var(--warn) 35%, transparent);
           border-radius: 10px; margin-bottom: 1rem; font-size: 0.88rem; color: var(--text-dim); }
.cm-warn code { color: var(--warn); }

/* graph stage */
.cm-stage { position: relative; background: var(--bg-elev); border: 1px solid var(--line);
            border-radius: var(--radius); overflow: hidden; margin-bottom: 1.2rem; }
.cm-canvas { display: block; width: 100%; height: 460px; cursor: grab; }
.cm-canvas:active { cursor: grabbing; }
.cm-hint { position: absolute; left: 0.9rem; bottom: 0.7rem; font-size: 0.75rem; color: var(--text-faint);
           pointer-events: none; }
.cm-tip { position: absolute; padding: 0.4rem 0.6rem; background: var(--bg); border: 1px solid var(--accent);
          border-radius: 8px; font-size: 0.8rem; pointer-events: none; opacity: 0;
          transition: opacity var(--dur) var(--ease); max-width: 22rem; z-index: 3; }
.cm-tip.on { opacity: 1; }
.cm-tip .t-loc { color: var(--text-faint); font-family: var(--mono); font-size: 0.74rem; }

/* cluster legend */
.cm-legend { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 1.2rem; }
.cm-chip { display: inline-flex; align-items: center; gap: 0.45rem; padding: 0.28rem 0.7rem;
           border: 1px solid var(--line); border-radius: 999px; font-size: 0.82rem; cursor: pointer;
           background: none; color: var(--text-dim); font-family: inherit;
           transition: border-color var(--dur) var(--ease), color var(--dur) var(--ease); }
.cm-chip:hover { color: var(--text); border-color: var(--accent); }
.cm-chip[aria-pressed="true"] { color: var(--text); border-color: currentColor; }
.cm-chip .dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; flex: none; }
.cm-chip .n { color: var(--text-faint); font-variant-numeric: tabular-nums; }
.cm-chip.muted-chip { opacity: 0.4; }

/* detail panels */
.cm-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
@media (max-width: 900px) { .cm-cols { grid-template-columns: 1fr; } }
.cm-panel { background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius);
            padding: 1rem 1.1rem; }
.cm-panel h3 { margin: 0 0 0.2rem; font-size: 0.8rem; letter-spacing: 0.08em; text-transform: uppercase;
               color: var(--text-faint); font-weight: 600; }
.cm-panel .sub { margin: 0 0 0.8rem; font-size: 0.8rem; color: var(--text-faint); }
.cm-row { display: flex; align-items: center; gap: 0.6rem; padding: 0.4rem 0;
          border-bottom: 1px solid var(--line-soft); }
.cm-row:last-child { border-bottom: none; }
.cm-row .dot { width: 0.55rem; height: 0.55rem; border-radius: 50%; flex: none; }
.cm-row .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cm-row .cm-deg { margin-left: auto; color: var(--text-faint); font-variant-numeric: tabular-nums; flex: none; }
.cm-bar { height: 3px; border-radius: 2px; flex: none; width: 3rem; background: var(--line); overflow: hidden; }
.cm-bar i { display: block; height: 100%; }
.cm-loc { background: none; border: 1px solid var(--line); border-radius: 6px; color: var(--text-faint);
          font-family: var(--mono); font-size: 0.72rem; padding: 0.1rem 0.4rem; cursor: pointer; flex: none;
          max-width: 14rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cm-loc:hover { color: var(--text); border-color: var(--accent); }
.cm-surprise { padding: 0.5rem 0; border-bottom: 1px solid var(--line-soft); font-size: 0.88rem; }
.cm-surprise:last-child { border-bottom: none; }
.cm-surprise .why { display: block; color: var(--text-faint); font-size: 0.8rem; margin-top: 0.15rem; }
.cm-arrow { color: var(--accent); }
.pbranch { font-family: var(--mono); }
.pact { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.pdetail h4 { margin: 1rem 0 0.5rem; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-faint); }
.timeline { display: flex; flex-direction: column; gap: 0.4rem; }
.tl-row { font-size: 0.82rem; color: var(--text-dim); padding: 0.35rem 0.5rem; background: var(--bg-elev);
  border: 1px solid var(--line); border-radius: 7px; overflow-wrap: anywhere; }
/* ---- Usage ---- */
.usage-top { font-size: 0.84rem; color: var(--text-dim); margin-bottom: 0.9rem;
  padding: 0.65rem 0.85rem; background: var(--bg-elev); border: 1px solid var(--line);
  border-radius: 10px; display: flex; flex-wrap: wrap; gap: 0.55rem; align-items: center; }
.usage-top .ut-label { color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.07em; font-size: 0.72rem; }
.usage-top .ut-item b { color: var(--accent); }
.use-badge { font-family: var(--mono); font-size: 0.72rem; color: #fbbf24; border: 1px solid #7a5c15;
  border-radius: 999px; padding: 0.05rem 0.45rem; margin-left: 0.45rem; }
.hk-match { font-family: var(--mono); font-size: 0.72rem; color: var(--accent); margin-right: 0.5rem;
  border: 1px solid var(--line); border-radius: 6px; padding: 0.05rem 0.4rem; }
.hk-cmd { font-family: var(--mono); font-size: 0.78rem; color: var(--text); overflow-wrap: anywhere; }

/* ---- Architectures ---- */
.arch-grid { display: grid; gap: 0.9rem; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }
.arch-card {
  background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 0.95rem 1rem; cursor: pointer; text-align: left; font: inherit; color: var(--text);
  display: flex; flex-direction: column; gap: 0.55rem; min-width: 0;
  transition: border-color var(--dur) var(--ease), transform var(--dur) var(--ease);
}
.arch-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.arch-card:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.arch-name { font-weight: 640; font-size: 1rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.arch-status { display: flex; align-items: center; gap: 0.4rem; font-size: 0.85rem; color: var(--text-dim); }
.arch-status .st-text { text-transform: capitalize; }
.arch-phase { font-size: 0.82rem; color: var(--text-faint); font-family: var(--mono);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.arch-bar { height: 8px; border-radius: 99px; background: var(--bg-elev-2);
  border: 1px solid var(--line-soft); overflow: hidden; }
.arch-bar > i { display: block; height: 100%; width: var(--w, 0%);
  background: linear-gradient(90deg, var(--brain), var(--accent)); transition: width var(--dur) var(--ease); }
.arch-foot { display: flex; align-items: baseline; justify-content: space-between; gap: 0.6rem;
  font-size: 0.78rem; color: var(--text-faint); font-variant-numeric: tabular-nums; }
.arch-foot .pct { color: var(--text-dim); font-family: var(--mono); }
.arch-badge { font-size: 0.7rem; font-family: var(--mono); padding: 0.12rem 0.5rem; border-radius: 999px;
  border: 1px solid var(--line); color: var(--text-dim); white-space: nowrap; }
.arch-badge.active { color: var(--ok); border-color: color-mix(in oklch, var(--ok) 40%, transparent); }
.arch-badge.paused { color: var(--warn); border-color: color-mix(in oklch, var(--warn) 40%, transparent); }
.arch-badge.completed { color: var(--bad); border-color: color-mix(in oklch, var(--bad) 40%, transparent); }
/* architecture detail */
.arch-detail-head { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1rem; }
.arch-detail-head h3 { margin: 0; font-size: 1.25rem; }
.arch-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
@media (max-width: 900px) { .arch-cols { grid-template-columns: 1fr; } }
.arch-step { display: flex; align-items: center; gap: 0.6rem; padding: 0.45rem 0;
  border-bottom: 1px solid var(--line-soft); font-size: 0.88rem; }
.arch-step:last-child { border-bottom: none; }
.arch-step .idx { color: var(--text-faint); font-family: var(--mono); font-size: 0.76rem; flex: none;
  min-width: 1.6rem; text-align: right; }
.arch-step .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.arch-step .mark { flex: none; }
.arch-step.current { color: var(--text); }
.arch-step.current .nm { font-weight: 640; }
.arch-step.done .nm { color: var(--text-faint); text-decoration: line-through; }
.arch-step .mark.done { color: var(--ok); }
.arch-step .mark.active { color: var(--accent); }
.arch-step .mark.pending { color: var(--text-faint); }
.arch-step .mark.skipped { color: var(--text-faint); }
.arch-step.skipped .nm { color: var(--text-faint); font-style: italic; }
.arch-step .pcount { margin-left: auto; font-family: var(--mono); font-size: 0.72rem; color: var(--text-faint); }
.arch-step .when { font-family: var(--mono); font-size: 0.72rem; color: var(--text-faint); }

/* repo / scope line under the detail title */
.arch-meta { display: flex; gap: 1rem; flex-wrap: wrap; font-family: var(--mono); font-size: 0.76rem; }

/* workflow flowchart — inline SVG, viewBox-scaled, no canvas and no libraries */
.fc-wrap { width: 100%; overflow-x: auto; }
/* The spine is taller than it is wide, so width is capped at the viewBox's own
   width: scaling it up to fill a wide panel would only make it taller. */
.fc svg { width: 100%; max-width: 404px; height: auto; display: block; }
.fc-box { fill: var(--bg-elev-2); stroke: var(--line); stroke-width: 1.2; }
.fc-node .fc-box { transition: stroke var(--dur) var(--ease), fill var(--dur) var(--ease); }
.fc-node { cursor: pointer; }
.fc-node:hover .fc-box { stroke: var(--accent); fill: var(--bg-elev-3, var(--bg-elev-2)); }
.fc-node:focus-visible { outline: none; }
.fc-node:focus-visible .fc-box { stroke: var(--accent); stroke-width: 2.4; }
.fc-node.done .fc-box { stroke: color-mix(in oklch, var(--ok) 40%, var(--line)); }
.fc-node.active .fc-box { stroke: var(--accent); stroke-width: 2; }
.fc-node.skipped .fc-box { stroke-dasharray: 3 3; }
.fc-name { fill: var(--text); font-size: 12px; font-weight: 600; }
.fc-node.done .fc-name { fill: var(--text-dim); }
.fc-node.skipped .fc-name { fill: var(--text-faint); font-style: italic; }
.fc-badge { fill: var(--text-faint); font-size: 10px; font-family: var(--mono); }
.fc-glyph { font-size: 12px; }
.fc-glyph.done { fill: var(--ok); }
.fc-glyph.active { fill: var(--accent); }
.fc-glyph.pending { fill: var(--text-faint); }
.fc-glyph.skipped { fill: var(--text-faint); opacity: 0.55; }
.fc-edge { stroke: var(--line); stroke-width: 1.4; fill: none; }
.fc-loop { stroke: var(--bad); stroke-width: 1.3; fill: none; stroke-dasharray: 5 4; opacity: 0.85; }
.fc-loop-label { fill: var(--bad); font-size: 10px; font-family: var(--mono); text-anchor: middle; }
.fc-loop-label-bg { fill: var(--bg); }
.fc-legend { display: flex; gap: 1.1rem; flex-wrap: wrap; margin-top: 0.5rem;
  font-size: 0.72rem; color: var(--text-faint); font-family: var(--mono); }
.fc-legend i { display: inline-block; width: 20px; height: 0; vertical-align: middle;
  margin-right: 0.35rem; border-top: 1.5px solid var(--line); }
.fc-legend i.loop { border-top: 1.5px dashed var(--bad); }
/* The active node breathes so the eye lands on it; stillness for anyone who asked. */
@keyframes fc-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
.fc-glyph.active { animation: fc-pulse 2.4s var(--ease) infinite; }
@media (prefers-reduced-motion: reduce) { .fc-glyph.active { animation: none; } }

/* detail view: spine on the left, everything that scrolls on the right */
.arch-detail-grid { display: grid; grid-template-columns: minmax(420px, 45%) 1fr;
  gap: 16px; align-items: start; }
/* The spine is the reference you read the tasks against, so it stays put. */
.arch-left { position: sticky; top: 12px; align-self: start; }
.arch-right > * + * { margin-top: 1rem; }
@media (max-width: 900px) {
  .arch-detail-grid { grid-template-columns: 1fr; }
  .arch-left { position: static; }
}
.fc-node.selected .fc-box { stroke: var(--accent); stroke-dasharray: none; }
.fc-node.selected .fc-name { fill: var(--text); }

/* template gallery (step 1 of the new-architecture wizard) */
.tpl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 0.6rem; }
.tpl-card { text-align: left; padding: 0.7rem 0.8rem; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--line); background: var(--bg-elev-2); color: inherit; font: inherit;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease); }
.tpl-card:hover { border-color: var(--accent); }
.tpl-card:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.tpl-card.sel { border-color: var(--accent); background: color-mix(in oklch, var(--accent) 10%, var(--bg-elev-2)); }
.tpl-head { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.25rem; }
.tpl-icon { font-size: 1rem; }
.tpl-name { font-weight: 640; font-size: 0.9rem; }
.tpl-meta { font-family: var(--mono); font-size: 0.7rem; color: var(--text-faint); margin-bottom: 0.3rem; }
/* two lines, then ellipsis — cards must stay the same height to scan as a grid */
.tpl-desc { font-size: 0.78rem; color: var(--text-dim); display: -webkit-box;
  -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tpl-chosen { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin: 0 0 0.9rem; }
.linkish { background: none; border: 0; padding: 0; font: inherit; font-size: 0.78rem;
  color: var(--accent); cursor: pointer; text-decoration: underline; }
.tpl-badge, .tpl-mini { display: inline-flex; align-items: center; gap: 0.3rem;
  font-family: var(--mono); font-size: 0.72rem; padding: 0.1rem 0.45rem; border-radius: 999px;
  border: 1px solid var(--line); color: var(--text-dim); }
.tpl-mini { border: 0; padding: 0; margin-left: auto; }
.tier { font-family: var(--mono); font-size: 0.62rem; font-weight: 700; padding: 0.02rem 0.3rem;
  border-radius: 4px; border: 1px solid currentColor; }
.tier.ts { color: var(--ok); }
.tier.tm { color: var(--warn); }
.tier.tl { color: var(--accent); }

/* auto-advance */
.manual-badge { font-family: var(--mono); font-size: 0.72rem; padding: 0.1rem 0.5rem;
  border-radius: 999px; color: var(--warn);
  border: 1px solid color-mix(in oklch, var(--warn) 40%, transparent); }
.aa-log { display: flex; flex-direction: column; gap: 0.25rem; }
.aa-row { display: flex; align-items: baseline; gap: 0.6rem; font-size: 0.8rem;
  padding: 0.2rem 0; border-bottom: 1px solid var(--line-soft); }
.aa-row:last-child { border-bottom: none; }
.aa-kind { font-family: var(--mono); color: var(--ok); }
.aa-kind.back { color: var(--warn); }
.aa-move { font-family: var(--mono); color: var(--text); }
.run-mode { font-family: var(--mono); font-size: 0.62rem; text-transform: uppercase;
  letter-spacing: 0.06em; margin-right: 0.35rem; color: var(--text-faint); }
.run-mode.plan { color: var(--warn); }
.run-mode.do { color: var(--accent); }
.aa-n { font-size: 0.72rem; color: var(--text-faint); }
.aa-when { margin-left: auto; font-family: var(--mono); font-size: 0.72rem; color: var(--text-faint); }

/* Overview: what is moving right now */
.flight-list { display: flex; flex-direction: column; gap: 0.3rem; }
.flight-row { display: grid; grid-template-columns: auto minmax(6rem, 1.4fr) auto auto 1fr auto;
  align-items: center; gap: 0.7rem; width: 100%; text-align: left; cursor: pointer;
  padding: 0.4rem 0.5rem; border: 1px solid transparent; border-radius: 8px;
  background: none; color: inherit; font: inherit;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease); }
.flight-row:hover { background: var(--bg-elev-2); border-color: var(--line); }
.flight-row:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.flight-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-faint); }
.flight-dot.live { background: var(--accent); animation: fc-pulse 1.4s var(--ease) infinite; }
@media (prefers-reduced-motion: reduce) { .flight-dot.live { animation: none; } }
.flight-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.flight-phase, .flight-tasks, .flight-pct { font-family: var(--mono); font-size: 0.74rem;
  color: var(--text-faint); }
.flight-pct { color: var(--text-dim); min-width: 2.6rem; text-align: right; }
.flight-bar { height: 4px; border-radius: 999px; background: var(--bg-elev-2); overflow: hidden; }
.flight-bar i { display: block; height: 100%; width: var(--w, 0%);
  background: linear-gradient(90deg, var(--warn), var(--accent)); }
@media (max-width: 720px) {
  .flight-row { grid-template-columns: auto 1fr auto; }
  .flight-phase, .flight-bar { display: none; }
}

/* a card whose architecture has an agent working on it right now */
.card-run { display: inline-flex; align-items: center; gap: 0.3rem; margin-left: auto;
  margin-right: 0.5rem; font-family: var(--mono); font-size: 0.68rem; color: var(--accent); }
.card-run .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor;
  animation: fc-pulse 1.4s var(--ease) infinite; }
@media (prefers-reduced-motion: reduce) { .card-run .dot { animation: none; } }

/* run state */
.run-pill { display: inline-flex; align-items: center; gap: 0.35rem; font-family: var(--mono);
  font-size: 0.72rem; padding: 0.12rem 0.5rem; border-radius: 999px;
  border: 1px solid var(--line); color: var(--text-dim); }
.run-pill .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.run-pill.running { color: var(--accent); border-color: color-mix(in oklch, var(--accent) 45%, transparent); }
.run-pill.done { color: var(--ok); border-color: color-mix(in oklch, var(--ok) 40%, transparent); }
.run-pill.failed, .run-pill.killed { color: var(--bad); border-color: color-mix(in oklch, var(--bad) 40%, transparent); }
.run-pill.interrupted { color: var(--warn); border-color: color-mix(in oklch, var(--warn) 40%, transparent); }
.run-pill.running .dot { animation: fc-pulse 1.4s var(--ease) infinite; }
@media (prefers-reduced-motion: reduce) { .run-pill.running .dot { animation: none; } }
.log-head { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.mem-meta { font-family: var(--mono); font-size: 0.72rem; color: var(--text-faint); }
.run-warn { margin-top: 0.5rem; padding: 0.45rem 0.6rem; border-radius: 8px; font-size: 0.8rem;
  color: var(--warn); border: 1px solid color-mix(in oklch, var(--warn) 35%, transparent);
  background: color-mix(in oklch, var(--warn) 8%, transparent); }
.log-head .sub { margin: 0; }
.log-box { margin-top: 0.6rem; max-height: 22rem; overflow: auto; background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; padding: 0.6rem 0.7rem; }
.log-box pre { margin: 0; font-family: var(--mono); font-size: 0.74rem; line-height: 1.5;
  white-space: pre-wrap; overflow-wrap: anywhere; color: var(--text-dim); }
.run-list { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-top: 0.55rem; }
.run-item { font-family: var(--mono); font-size: 0.7rem; padding: 0.1rem 0.45rem;
  border-radius: 999px; border: 1px solid var(--line); background: var(--bg-elev-2);
  color: var(--text-faint); cursor: pointer; }
.run-item:hover { border-color: var(--accent); color: var(--text); }
.run-item.sel { border-color: var(--accent); color: var(--text); }
.btn[disabled] { opacity: 0.45; cursor: not-allowed; }

/* per-phase playbook chips */
.pb-groups { display: flex; flex-direction: column; gap: 0.55rem; }
.pb-line { display: flex; align-items: baseline; gap: 0.5rem; flex-wrap: wrap; }
.pb-line > .k { flex: none; width: 4.2rem; font-size: 0.72rem; font-family: var(--mono);
  text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-faint); }
.chip { font-size: 0.76rem; font-family: var(--mono); padding: 0.12rem 0.5rem;
  border-radius: 999px; border: 1px solid var(--line); background: var(--bg-elev-2);
  color: var(--text-dim); cursor: pointer;
  transition: border-color var(--dur) var(--ease), color var(--dur) var(--ease); }
.chip:hover { border-color: var(--accent); color: var(--text); }
.chip:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.pb-none { font-size: 0.78rem; color: var(--text-faint); font-style: italic; }
/* a task highlighted by clicking its phase in the flowchart */
.task-row.hl { background: color-mix(in oklch, var(--accent) 12%, transparent); border-radius: 7px; }

/* context files — a referenced path that is gone says so instead of 404ing */
.ctx-row { display: flex; align-items: baseline; gap: 0.5rem; padding: 0.3rem 0.35rem;
  border-bottom: 1px solid var(--line-soft); font-size: 0.85rem; cursor: pointer;
  border-radius: 7px; transition: background var(--dur) var(--ease); }
.ctx-row:last-child { border-bottom: none; }
.ctx-row:hover { background: var(--bg-elev-2); }
.ctx-row .ctx-mark { flex: none; }
.ctx-row .ctx-dir { font-family: var(--mono); font-size: 0.72rem; color: var(--text-faint);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ctx-row .ctx-miss { margin-left: auto; flex: none; font-family: var(--mono);
  font-size: 0.7rem; color: var(--warn); }
.ctx-row.missing .ctx-path { color: var(--text-faint); text-decoration: line-through; }

/* architecture actions + task columns */
.arch-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-left: auto; }
.arch-sec h4 { margin: 0 0 0.6rem; font-size: 0.78rem; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--text-faint); }
.task-group { margin-bottom: 1rem; }
.task-group:last-of-type { margin-bottom: 0; }
.task-group > .lbl { font-size: 0.74rem; font-family: var(--mono); color: var(--text-faint);
  text-transform: uppercase; letter-spacing: 0.06em; display: block; margin-bottom: 0.3rem; }
.task-group > .lbl .n { color: var(--text-dim); }
.task-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.15rem 0;
  border-bottom: 1px solid var(--line-soft); font-size: 0.86rem; }
.task-row:last-child { border-bottom: none; }
/* The row's label is the button, so click-to-advance never swallows the select. */
.task-main { flex: 1 1 auto; min-width: 0; display: flex; align-items: baseline; gap: 0.5rem;
  padding: 0.3rem 0.35rem; margin: 0; border: 1px solid transparent; border-radius: 7px;
  background: none; font: inherit; color: inherit; text-align: left; cursor: pointer;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease); }
.task-main:hover { background: var(--bg-elev-2); border-color: var(--line); }
.task-main:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.task-row .ttl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-row.done .ttl { color: var(--text-faint); text-decoration: line-through; }
.task-row .pri { flex: none; font-size: 0.68rem; font-family: var(--mono); padding: 0.05rem 0.4rem;
  border-radius: 999px; border: 1px solid var(--line); color: var(--text-faint); }
.task-row .pri.p0 { color: var(--bad); border-color: color-mix(in oklch, var(--bad) 40%, transparent); }
.task-row .pri.p1 { color: var(--warn); border-color: color-mix(in oklch, var(--warn) 40%, transparent); }
.task-row .tphase { flex: none; font-family: var(--mono); font-size: 0.72rem; color: var(--text-faint); }
.task-row .tflag { flex: none; font-family: var(--mono); font-size: 0.7rem; color: var(--warn); }
.task-row .task-status { flex: none; font-size: 0.74rem; padding: 0.15rem 0.3rem; }
.task-add { display: flex; gap: 0.45rem; flex-wrap: wrap; align-items: center;
  margin-top: 0.9rem; padding-top: 0.9rem; border-top: 1px solid var(--line); }
.task-add input[type=text] { flex: 1 1 160px; min-width: 0; }
.task-add select { flex: none; }
.empty-note { color: var(--text-faint); font-size: 0.85rem; font-style: italic; }

/* ---- Modal ---- */
.modal {
  position: fixed; inset: 0; display: none; place-items: center;
  background: oklch(0% 0 0 / 0.62); backdrop-filter: blur(5px); z-index: 40;
  padding: clamp(0.8rem, 2vw, 2rem);
}
.modal.open { display: grid; }
.dialog {
  width: min(1080px, 100%); height: min(82vh, 860px);
  background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: 0 24px 80px oklch(0% 0 0 / 0.52);
  display: grid; grid-template-rows: auto 1fr auto; overflow: hidden;
}
/* A phase pick is one short question — the full-height editor dialog dwarfs it. */
.dialog.compact { width: min(460px, 100%); height: auto; }
.dialog.compact .dialog-body { display: block; }
.fld { display: block; }
.fld > span { display: block; font-size: 0.78rem; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-faint); margin-bottom: 0.35rem; }
.fld select, .fld input, .fld textarea { width: 100%; }
.fld > span em { font-style: normal; opacity: 0.7; }
.dialog-head, .dialog-foot {
  padding: 0.9rem 1rem; border-bottom: 1px solid var(--line);
  display: flex; gap: 0.75rem; align-items: center;
}
.dialog-foot { border-top: 1px solid var(--line); border-bottom: 0; justify-content: flex-end; }
.dialog-title { min-width: 0; flex: 1; }
.dialog-title h3 { margin: 0; font-size: 1rem; overflow-wrap: anywhere; }
.dialog-title p { margin: 0.15rem 0 0; color: var(--text-faint); font: 0.74rem var(--mono); overflow-wrap: anywhere; }
.dialog-body { padding: 1rem; min-height: 0; display: grid; overflow-y: auto; }
.editor {
  width: 100%; height: 100%; min-height: 0; resize: none;
  font: 0.86rem/1.55 var(--mono); white-space: pre; overflow: auto;
}
.close-x {
  width: 34px; height: 34px; border-radius: 8px; border: 1px solid var(--line);
  background: transparent; color: var(--text-dim); cursor: pointer; font-size: 1.05rem;
}
.close-x:hover { color: var(--text); border-color: var(--accent); }

.muted { color: var(--text-faint); }
/* Approval dialog: the warning has to out-rank the body text it sits between,
   because it is the one line that should stop a reflex click. */
.warn-line { color: var(--warn); border-left: 2px solid var(--warn);
  padding-left: 0.6rem; margin: 0.7rem 0; }
.hint { color: var(--text-faint); font-size: 0.86rem; margin-top: 0.7rem; }
.loading { color: var(--text-dim); padding: 2rem 0; display:flex; align-items:center; gap:0.6rem; }
.spinner { width: 16px; height: 16px; border: 2px solid var(--line);
  border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty { color: var(--text-faint); padding: 2rem; text-align: center; border: 1px dashed var(--line);
  border-radius: var(--radius); }
.count-pill { font-family: var(--mono); font-size: 0.8rem; color: var(--text-faint); }
.toast { position: fixed; bottom: 1.2rem; right: 1.2rem; background: var(--bg-elev-2);
  border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 0.7rem 1rem;
  box-shadow: 0 10px 30px oklch(0% 0 0 / 0.4); transform: translateY(20px); opacity: 0;
  transition: transform var(--dur) var(--ease), opacity var(--dur) var(--ease); pointer-events: none; z-index: 50; }
.toast.show { transform: none; opacity: 1; }
.toast.bad { border-color: var(--bad); }

@media (max-width: 820px) {
  body { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; flex-direction: row; flex-wrap: wrap;
    align-items: center; border-right: none; border-bottom: 1px solid var(--line); }
  .brand { padding: 0.3rem 0.6rem; width: 100%; }
  .nav-spacer, .nav-foot { display: none; }
  .nav-btn { flex: 1 1 auto; justify-content: center; }
  .overview-grid { grid-template-columns: 1fr; }
  .panel-row, .scope-line { grid-template-columns: 1fr; gap: 0.25rem; }
  .scope-num { text-align: left; }
  .add-memory-row { grid-template-columns: 1fr; }
}

/* ---- Harness Studio ---- */
.harness-grid { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 1.25rem; margin-bottom: 1.25rem; }
@media (max-width: 900px) { .harness-grid { grid-template-columns: 1fr; } }
.harness-card { background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 1.1rem; display: flex; flex-direction: column; gap: 0.85rem; }
.harness-card h3 { margin: 0; font-size: 1.05rem; display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
.harness-form-group { display: flex; flex-direction: column; gap: 0.35rem; }
.harness-form-group label { font-size: 0.82rem; font-weight: 600; color: var(--text-dim); }
.harness-actions { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-top: 0.25rem; }
.harness-alert { background: oklch(66% 0.2 25 / 0.15); border: 1px solid var(--bad); color: var(--bad); padding: 0.85rem 1rem; border-radius: 8px; font-size: 0.88rem; display: flex; flex-direction: column; gap: 0.3rem; }
.harness-alert.pass { background: oklch(75% 0.18 145 / 0.15); border-color: var(--ok); color: var(--ok); }
.harness-terminal { background: #0c0f14; border: 1px solid #1a2233; border-radius: 8px; padding: 0.85rem 1rem; font-family: var(--mono); font-size: 0.82rem; color: #a5b4fc; max-height: 280px; overflow-y: auto; white-space: pre-wrap; line-height: 1.45; }
.harness-stepper { display: flex; flex-direction: column; gap: 0.4rem; max-height: 320px; overflow-y: auto; }
.harness-step-item { display: flex; align-items: baseline; gap: 0.6rem; padding: 0.4rem 0.6rem; border-radius: 6px; background: var(--bg-elev-2); border: 1px solid var(--line-soft); font-size: 0.82rem; }
.harness-step-item .badge { font-family: var(--mono); font-size: 0.72rem; padding: 0.05rem 0.4rem; border-radius: 4px; background: var(--bg); color: var(--accent); }
.hubs-grid { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.hub-chip { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.25rem 0.6rem; border-radius: 6px; border: 1px solid var(--line); font-size: 0.8rem; background: var(--bg-elev-2); color: var(--text-dim); }
.hub-chip .hub-deg { font-family: var(--mono); font-size: 0.7rem; font-weight: bold; color: var(--accent); }

/* ---- Assistant / Ask Brain ---- */
.chat-container { display: flex; flex-direction: column; height: calc(100vh - 145px); background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.chat-messages { flex: 1; overflow-y: auto; padding: 1.25rem; display: flex; flex-direction: column; gap: 1.1rem; }
.chat-msg { display: flex; flex-direction: column; gap: 0.35rem; max-width: 85%; }
.chat-msg.user { align-self: flex-end; }
.chat-msg.ai { align-self: flex-start; }
.chat-msg-header { display: flex; align-items: center; gap: 0.5rem; font-size: 0.8rem; color: var(--text-dim); }
.chat-msg.user .chat-msg-header { justify-content: flex-end; }
.chat-msg-bubble { padding: 0.85rem 1.1rem; border-radius: var(--radius-sm); line-height: 1.55; font-size: 0.92rem; word-break: break-word; white-space: pre-wrap; }
.chat-msg.user .chat-msg-bubble { background: var(--accent); color: #0b101b; font-weight: 500; border-bottom-right-radius: 2px; }
.chat-msg.ai .chat-msg-bubble { background: var(--bg-elev-2); border: 1px solid var(--line-soft); color: var(--text); border-bottom-left-radius: 2px; }
.chat-grounding { margin-top: 0.6rem; padding: 0.55rem 0.8rem; background: oklch(14% 0.01 265); border: 1px solid var(--line-soft); border-radius: 6px; font-size: 0.8rem; }
.chat-grounding summary { cursor: pointer; color: var(--accent); font-weight: 600; user-select: none; }
.chat-grounding ul { margin: 0.4rem 0 0.1rem 1.1rem; padding: 0; color: var(--text-dim); }
.chat-grounding li { margin-bottom: 0.25rem; line-height: 1.4; }
.chat-grounding .score { font-family: var(--mono); color: var(--ok); font-size: 0.72rem; margin-right: 0.35rem; }
.chat-input-bar { padding: 0.85rem 1rem; background: var(--bg); border-top: 1px solid var(--line); display: flex; gap: 0.6rem; align-items: flex-end; }
.chat-input { flex: 1; background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius-sm); color: var(--text); font-family: inherit; font-size: 0.92rem; padding: 0.65rem 0.85rem; resize: none; min-height: 44px; max-height: 140px; outline: none; line-height: 1.4; box-sizing: border-box; }
.chat-input:focus { border-color: var(--accent); }
.chat-send-btn { height: 44px; padding: 0 1.25rem; font-weight: 600; white-space: nowrap; }
</style>
</head>
<body>
<nav class="sidebar" aria-label="Main navigation">
  <div class="brand">
    <span class="glyph">🧠</span>
    <div><small>Shared Brain</small><h1>Control Panel</h1></div>
  </div>
  <button class="nav-btn" data-view="overview" aria-current="true"><span class="ico">◎</span>Overview</button>
  <button class="nav-btn" data-view="assistant"><span class="ico">💬</span>Ask Brain</button>
  <button class="nav-btn" data-view="projects"><span class="ico">📁</span>Projects</button>
  <button class="nav-btn" data-view="codemap"><span class="ico">🗺</span>Code Map</button>
  <button class="nav-btn" data-view="architectures"><span class="ico">🏗</span>Architectures</button>
  <button class="nav-btn" data-view="harness"><span class="ico">⚡</span>Harness Studio</button>
  <button class="nav-btn" data-view="memory"><span class="ico">🧠</span>Memory</button>
  <button class="nav-btn" data-view="skills"><span class="ico">✦</span>Skills</button>
  <button class="nav-btn" data-view="agents"><span class="ico">⌘</span>Agents</button>
  <button class="nav-btn" data-view="mcp"><span class="ico">⇄</span>MCP</button>
  <button class="nav-btn" data-view="rules"><span class="ico">§</span>Rules</button>
  <button class="nav-btn" data-view="hooks"><span class="ico">🪝</span>Hooks</button>
  <div class="nav-spacer"></div>
  <div class="nav-foot"><span class="pulse"></span>localhost:8899</div>
</nav>

<main>
  <!-- OVERVIEW -->
  <section id="view-overview" class="view active" role="tabpanel" aria-label="Overview">
    <div class="view-head">
      <h2>Mission Control</h2>
      <p>Live status of the local memory stack and AI knowledge assets.</p>
    </div>
    <div id="tiles" class="tiles"><div class="loading"><span class="spinner"></span>Reading system health…</div></div>
    <div id="overview-detail"><div class="loading"><span class="spinner"></span>Preparing mission detail…</div></div>
  </section>

  <!-- MEMORY -->
  <section id="view-memory" class="view" role="tabpanel" aria-label="Memory">
    <div class="view-head">
      <h2>Memory</h2>
      <p>Browse, search, add and prune the shared brain. <span class="muted">🧠 = global tier · project searches include it.</span></p>
    </div>

    <div class="toolbar">
      <input type="search" id="mem-search" class="search-box" placeholder="Semantic search… (Enter)">
      <select id="mem-scope" aria-label="Filter by scope"><option value="">All scopes</option></select>
      <select id="mem-agent" aria-label="Filter by agent"><option value="">All agents</option></select>
      <button class="btn primary" id="mem-add-open">Add</button>
      <button class="btn ghost" id="mem-refresh">↻ Refresh</button>
      <span class="count-pill" id="mem-count"></span>
    </div>

    <div id="mem-body"><div class="loading"><span class="spinner"></span>Loading memories…</div></div>
  </section>

  <!-- SKILLS -->
  <section id="view-skills" class="view" role="tabpanel" aria-label="Skills">
    <div class="view-head hd-row"><div><h2>Skills</h2><p>Installed <code>SKILL.md</code> capabilities across Claude, Codex, skills and plugins.</p></div><button class="btn primary add-btn" id="add-skill">+ New skill</button></div>
    <div class="toolbar">
      <input type="search" id="skill-search" class="search-box" placeholder="Filter skills by name or description…">
      <span class="count-pill" id="skill-count"></span>
    </div>
    <div id="skill-body"><div class="loading"><span class="spinner"></span>Scanning skills…</div></div>
  </section>

  <!-- AGENTS -->
  <section id="view-agents" class="view" role="tabpanel" aria-label="Agents">
    <div class="view-head hd-row"><div><h2>Agents</h2><p>Sub-agents defined across local AI tool homes.</p></div><button class="btn primary add-btn" id="add-agent">+ New agent</button></div>
    <div id="agent-body"><div class="loading"><span class="spinner"></span>Loading agents…</div></div>
  </section>

  <!-- MCP -->
  <section id="view-mcp" class="view" role="tabpanel" aria-label="MCP servers">
    <div class="view-head hd-row"><div><h2>MCP Servers</h2><p>Configured Model Context Protocol servers across Claude, Codex, Cursor, VS Code and Cline.</p></div><button class="btn primary add-btn" id="add-mcp">+ Add server</button></div>
    <div id="mcp-body"><div class="loading"><span class="spinner"></span>Reading .claude.json…</div></div>
  </section>

  <!-- RULES -->
  <section id="view-rules" class="view" role="tabpanel" aria-label="Rules">
    <div class="view-head hd-row"><div><h2>Rules</h2><p>Coding standards and conventions across local AI tool homes.</p></div><button class="btn primary add-btn" id="add-rule">+ New rule</button></div>
    <div id="rules-body"><div class="loading"><span class="spinner"></span>Loading rules…</div></div>
  </section>

  <!-- HOOKS -->
  <section id="view-hooks" class="view" role="tabpanel" aria-label="Hooks">
    <div class="view-head"><h2>Hooks</h2><p>Deterministic automations wired in <code>settings.json</code> — global and per-project. These run unconditionally; the model can't skip them.</p></div>
    <div id="hooks-body"><div class="loading"><span class="spinner"></span>Reading settings…</div></div>
  </section>

  <!-- PROJECTS -->
  <section id="view-projects" class="view" role="tabpanel" aria-label="Projects">
    <div class="view-head hd-row"><div><h2>Projects</h2><p>Every project in <code>~/Projeler</code> — git status, last activity, and which agent did what.</p></div><button class="btn ghost add-btn" id="proj-refresh">↻ Refresh</button></div>
    <div id="proj-body"><div class="loading"><span class="spinner"></span>Scanning projects…</div></div>
  </section>

  <!-- CODE MAP -->
  <section id="view-codemap" class="view" role="tabpanel" aria-label="Code map">
    <div class="view-head hd-row">
      <div><h2>Code Map</h2><p>What this repo is made of, and what holds it together. Read-only — the dashboard draws the graph, it never builds it.</p></div>
      <div class="hd-actions">
        <select id="cm-repo" class="btn ghost" aria-label="Repository"></select>
        <button class="btn ghost" id="cm-refresh" title="Re-read the graph from disk">↻ Refresh</button>
      </div>
    </div>
    <div id="cm-body"><div class="loading"><span class="spinner"></span>Loading repos…</div></div>
  </section>

  <!-- ARCHITECTURES -->
  <section id="view-architectures" class="view" role="tabpanel" aria-label="Architectures">
    <div class="view-head hd-row">
      <div><h2>Architectures</h2><p>Long-running build plans — where each one stands, phase by phase.</p></div>
      <div class="hd-actions">
        <button class="btn ghost" id="arch-refresh" title="Re-read from disk">↻ Refresh</button>
        <button class="btn primary add-btn" id="arch-new">+ New Architecture</button>
      </div>
    </div>
    <div id="arch-body"><div class="loading"><span class="spinner"></span>Loading architectures…</div></div>
  </section>

  <!-- HARNESS STUDIO -->
  <section id="view-harness" class="view" role="tabpanel" aria-label="Harness Studio">
    <div class="view-head hd-row">
      <div>
        <h2>Agent Execution &amp; Evaluation Harness</h2>
        <p>Pre-flight context assembly, trajectory tracing, safety guardrails &amp; post-flight verification.</p>
      </div>
      <div class="hd-actions">
        <button class="btn ghost" id="harness-refresh" title="Re-read runs &amp; hubs">↻ Refresh</button>
      </div>
    </div>
    <div id="harness-body">
      <div class="loading"><span class="spinner"></span>Loading Harness Studio…</div>
    </div>
  </section>

  <!-- ASSISTANT / ASK BRAIN -->
  <section id="view-assistant" class="view" role="tabpanel" aria-label="Ask Brain">
    <div class="view-head hd-row">
      <div>
        <h2>Ask Shared Brain</h2>
        <p>Interactive AI assistant grounded in your long-term memories, active rules, and architectural knowledge.</p>
      </div>
      <div class="hd-actions">
        <select id="chat-scope" class="btn ghost" aria-label="Scope">
          <option value="personal">personal (Shared Brain)</option>
        </select>
        <button class="btn ghost" id="chat-clear" title="Clear chat history">Clear Chat</button>
      </div>
    </div>
    <div id="chat-container" class="chat-container">
      <div id="chat-messages" class="chat-messages">
        <div class="chat-msg ai">
          <div class="chat-msg-header"><span class="chat-avatar">🧠</span><strong>Shared Brain</strong><span class="chat-time">Ready</span></div>
          <div class="chat-msg-bubble">Hello! I am your Shared Brain assistant running on your local network. I can recall past architectural decisions, check rules, verify gotchas, and answer questions grounded in your long-term memory. How can I help you?</div>
        </div>
      </div>
      <div class="chat-input-bar">
        <textarea id="chat-input" class="chat-input" placeholder="Ask anything about your projects, rules, or memories... (Shift+Enter for newline, Enter to send)" rows="2"></textarea>
        <button id="chat-send" class="btn primary chat-send-btn">Send ➔</button>
      </div>
    </div>
  </section>
</main>

<div class="modal" id="phase-modal" role="dialog" aria-modal="true" aria-labelledby="phase-modal-title">
  <div class="dialog compact">
    <div class="dialog-head">
      <div class="dialog-title">
        <h3 id="phase-modal-title">Update Phase</h3>
        <p id="phase-modal-meta"></p>
      </div>
      <button class="close-x" id="phase-close" aria-label="Close">✕</button>
    </div>
    <div class="dialog-body">
      <label class="fld" for="phase-select">
        <span>Current phase</span>
        <select id="phase-select"></select>
      </label>
      <p class="muted" id="phase-hint" style="margin:0.7rem 0 0"></p>
    </div>
    <div class="dialog-foot">
      <span class="muted" id="phase-status"></span>
      <button class="btn ghost" id="phase-cancel">Cancel</button>
      <button class="btn primary" id="phase-save">Update</button>
    </div>
  </div>
</div>

<div class="modal" id="detail-modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
  <div class="dialog">
    <div class="dialog-head">
      <div class="dialog-title">
        <h3 id="modal-title">Detail</h3>
        <p id="modal-meta"></p>
      </div>
      <button class="close-x" id="modal-close" aria-label="Close">✕</button>
    </div>
    <div class="dialog-body" id="modal-body">
      <textarea class="editor" id="modal-editor" spellcheck="false"></textarea>
    </div>
    <div class="dialog-foot">
      <span class="muted" id="modal-status"></span>
      <button class="btn ghost" id="modal-cancel">Close</button>
      <button class="btn primary" id="modal-save">Save</button>
    </div>
  </div>
</div>

<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script>
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];
const esc = s => (s==null?"":String(s)).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Delete affordance shared across skills/agents/mcp/rules items.
const delBtn = (delPath, kind, name='') =>
  `<button class="del-x" data-del="${esc(delPath)}" data-delkind="${kind}" data-delname="${esc(name)}" title="Delete" aria-label="Delete ${esc(name)}">✕</button>`;

function bindDeletes(rootSel, reload) {
  $$(rootSel + ' .del-x').forEach(btn => btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const { del, delkind, delname } = btn.dataset;
    const label = delname || del.split('/').pop();
    if (!confirm(`Delete "${label}"?\nThis removes it from disk.`)) return;
    try {
      if (delkind === 'mcp') {
        await api('/api/mcp?name=' + encodeURIComponent(delname) +
                  '&path=' + encodeURIComponent(del), { method: 'DELETE' });
      } else {
        // 'file' deletes the path; 'skilldir' deletes the skill directory
        const target = delkind === 'skilldir' ? del.replace(/\/SKILL\.md$/i, '') : del;
        await api('/api/content?path=' + encodeURIComponent(target), { method: 'DELETE' });
      }
      toast('Deleted ' + label);
      reload();
    } catch (err) { toast('Delete failed: ' + err.message, true); }
  }));
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}
function toast(msg, bad=false) {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast show' + (bad?' bad':'');
  setTimeout(() => t.className = 'toast', 2600);
}
function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso); if (isNaN(d)) return iso;
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  if (diff < 604800) return Math.floor(diff/86400) + 'd ago';
  return d.toISOString().slice(0,10);
}
const SHARED = 'personal';
const isShared = s => s === SHARED;
const scopeLabel = s => isShared(s) ? '🧠 Shared Brain' : (s || '(none)');
let modalState = null;
let memoryById = new Map();

function setModal(open) {
  $('#detail-modal').classList.toggle('open', open);
  if (!open) modalState = null;
}
function openModal({ title, meta='', content='', editable=true, state }) {
  modalState = state;
  // Default back to the tall editor dialog; short-form callers opt into compact.
  $('#detail-modal .dialog').classList.remove('compact');
  const body = $('#modal-body');
  if (!$('#modal-editor') || !$('#modal-editor').classList.contains('editor')) {
    body.innerHTML = '<textarea class="editor" id="modal-editor" spellcheck="false"></textarea>';
  }
  $('#modal-title').textContent = title;
  $('#modal-meta').textContent = meta;
  $('#modal-editor').value = content;
  $('#modal-editor').readOnly = !editable;
  $('#modal-save').style.display = editable ? '' : 'none';
  $('#modal-save').textContent = 'Save';
  $('#modal-status').textContent = editable ? 'Editable' : 'Read only';
  setModal(true);
  $('#modal-editor').focus();
}
async function openFileModal(path, title, meta='') {
  openModal({ title, meta: meta || path, content: 'Loading…', editable: false, state: null });
  try {
    const data = await api('/api/content?path=' + encodeURIComponent(path));
    openModal({
      title: title || data.name,
      meta: data.path,
      content: data.content,
      editable: data.editable,
      state: { type: 'file', path: data.path },
    });
  } catch (e) {
    openModal({ title: 'Failed to open', meta: path, content: e.message, editable: false, state: null });
  }
}
function openMemoryModal(id) {
  const item = memoryById.get(id);
  if (!item) return toast('Memory not loaded', true);
  openModal({
    title: 'Memory',
    meta: `${scopeLabel(item.run_id || '(none)')} · ${item.agent_id || '(none)'} · ${item.created_at || ''}`,
    content: item.memory || '',
    editable: true,
    state: { type: 'memory', id },
  });
}
function openAddMemoryModal() {
  openModal({
    title: 'Add memory',
    meta: 'scope and agent are set below the text',
    content: '',
    editable: true,
    state: { type: 'new-memory' },
  });
  $('#modal-save').textContent = 'Store memory';
  const body = $('#modal-body');
  body.innerHTML = `
    <div class="add-memory-grid">
      <textarea class="editor" id="modal-editor" placeholder="A durable fact, preference, or decision worth remembering..." spellcheck="false"></textarea>
      <div class="add-memory-row">
        <input type="text" id="modal-scope" placeholder="scope (default: personal / shared brain)">
        <input type="text" id="modal-agent" placeholder="agent (default: claude-code)">
      </div>
    </div>`;
  $('#modal-editor').focus();
}
const STARTERS = {
  rule: n => `# ${n||'Rule'}\n\n> Convention or standard for local AI tools.\n\n- First point\n`,
  agent: n => `---\nname: ${n||'agent-name'}\ndescription: When this agent should be used.\ntools: Read, Grep, Glob\n---\n\nYou are ${n||'the agent'}. Describe its responsibilities here.\n`,
  skill: n => `---\nname: ${n||'skill-name'}\ndescription: What this skill does and when to use it.\n---\n\n# ${n||'Skill'}\n\nInstructions for the skill.\n`,
};
async function openNewFileModal(kind) {
  const dir = { rule: 'rules', agent: 'agents', skill: 'skills' }[kind];
  openModal({ title: 'New ' + kind, meta: 'Creates a file under ~/.claude/' + dir,
    content: '', editable: true, state: { type: 'new-file', kind } });
  $('#modal-save').textContent = 'Create ' + kind;
  let folderRow = '';
  if (kind === 'rule') {
    let folders = ['(root)', 'common', 'web', 'custom'];
    try {
      const data = await api('/api/rules');
      const set = new Set(['(root)', 'custom']);
      Object.keys(data.groups || {}).forEach(k => {
        const f = k.split('/').slice(1).join('/');
        if (f) set.add(f);
      });
      folders = [...set].sort((a, b) =>
        a === '(root)' ? -1 : b === '(root)' ? 1 : a.localeCompare(b));
    } catch (e) {}
    const opts = folders.map(f =>
      `<option value="${esc(f)}"${f === 'custom' ? ' selected' : ''}>${esc(f)}</option>`).join('');
    folderRow = `<select id="nf-folder-sel">${opts}<option value="__new__">＋ new folder…</option></select>
      <input type="text" id="nf-folder-new" placeholder="new folder name" style="display:none">`;
  }
  $('#modal-body').innerHTML = `
    <div class="form-grid">
      <input type="text" id="nf-name" placeholder="${kind} name (e.g. my-${kind})">
      ${folderRow}
    </div>
    <textarea class="editor" id="modal-editor" spellcheck="false" style="margin-top:.6rem"></textarea>`;
  const fsel = $('#nf-folder-sel');
  if (fsel) fsel.addEventListener('change', () => {
    const ni = $('#nf-folder-new');
    ni.style.display = fsel.value === '__new__' ? '' : 'none';
    if (fsel.value === '__new__') ni.focus();
  });
  let last = STARTERS[kind]('');
  $('#modal-editor').value = last;
  $('#nf-name').addEventListener('input', () => {
    const ed = $('#modal-editor');
    if (ed.value === last) ed.value = last = STARTERS[kind]($('#nf-name').value.trim());
  });
  $('#nf-name').focus();
}
function openAddMcpModal() {
  openModal({ title: 'Add MCP server', meta: 'Adds a stdio server to ~/.claude.json (global). Restart the AI tool to load it.',
    content: '', editable: true, state: { type: 'new-mcp' } });
  $('#modal-save').textContent = 'Add server';
  $('#modal-body').innerHTML = `
    <div class="form-grid">
      <input type="text" id="mcp-name" placeholder="server name (e.g. my-server)">
      <input type="text" id="mcp-cmd" placeholder="command (e.g. npx, or /path/to/python)">
      <input type="text" id="mcp-args" placeholder="args, space-separated (e.g. -y @scope/pkg)">
    </div>`;
  $('#mcp-name').focus();
}
/* Two steps, because the template decides the shape of everything that follows:
   pick the shape first, then name the work. */
let newArch = { template: null, repos: null };

/* Approving is the one irreversible-by-intent action in the panel: it is a
   person putting their name on work an agent did. A button that sits on the
   toolbar in every phase turns that into a reflex, so the control only exists
   while a gate is holding — and even then it asks rather than moves. */
function openApproveModal(id, phase, next, order, idx, open) {
  openModal({ title: 'Approve ' + phase + '?', meta: '', content: '', editable: true,
    state: { type: 'approve-gate', id, order, target: idx + 1 } });
  $('#detail-modal .dialog').classList.add('compact');
  $('#modal-save').textContent = 'Approve & continue';
  $('#modal-status').textContent = '';
  $('#modal-body').innerHTML = `
    <p>You are approving <b>${esc(phase)}</b>. The project moves to
       <b>${esc(next || 'the next phase')}</b> and this gate is recorded as done.</p>
    ${open ? `<p class="warn-line">⚠️ ${open} task${open === 1 ? '' : 's'} in this phase
       ${open === 1 ? 'is' : 'are'} still open. Approving over open work is allowed —
       just not by accident.</p>` : ''}
    <p class="hint">Nothing here is undone automatically. To reject instead, close
       this and use <b>← Send back</b>.</p>`;
}

async function openNewArchModal() {
  newArch = { template: null, repos: newArch.repos };
  openModal({ title: 'New architecture', meta: 'Step 1 of 2 — pick a template',
    content: '', editable: true, state: { type: 'new-arch', step: 1 } });
  $('#modal-save').textContent = 'Next';
  $('#modal-save').disabled = true;
  $('#modal-status').textContent = '';
  $('#detail-modal .dialog').classList.add('compact');
  $('#modal-body').innerHTML = '<div class="loading"><span class="spinner"></span>Loading templates…</div>';

  let data;
  try {
    data = await api('/api/architecture/templates');
  } catch (e) {
    $('#modal-body').innerHTML = `<div class="empty">Could not load templates: ${esc(e.message)}</div>`;
    return;
  }
  const warn = (data.warnings || []).length
    ? `<p class="pb-none">⚠️ ${data.warnings.length} template file(s) skipped: ${esc(data.warnings.join(' · '))}</p>` : '';
  $('#modal-body').innerHTML = `${warn}<div class="tpl-grid">${(data.results || []).map(t => `
    <button type="button" class="tpl-card" data-tpl="${esc(t.id)}">
      <div class="tpl-head"><span class="tpl-icon">${esc(t.icon || '📐')}</span>
        <span class="tpl-name">${esc(t.name)}</span>${tierBadge(t.tier)}</div>
      <div class="tpl-meta">${t.phases.length} phases · ${t.loops.length} loop${t.loops.length === 1 ? '' : 's'}${
        t.source === 'user' ? ' · user' : ''}</div>
      <div class="tpl-desc">${esc(t.description || '')}</div>
    </button>`).join('')}</div>`;

  const cards = $$('#modal-body .tpl-card');
  cards.forEach(card => card.addEventListener('click', () => {
    cards.forEach(c => c.classList.toggle('sel', c === card));
    newArch.template = (data.results || []).find(t => t.id === card.dataset.tpl) || null;
    $('#modal-save').disabled = !newArch.template;
  }));
}

async function newArchStep2() {
  const t = newArch.template;
  modalState.step = 2;
  $('#modal-meta').textContent = 'Step 2 of 2 — name the work';
  $('#modal-save').textContent = 'Create';
  $('#modal-save').disabled = false;
  $('#modal-body').innerHTML = `
    <p class="tpl-chosen">
      <span class="tpl-badge">${esc(t.icon || '📐')} ${esc(t.name)}${tierBadge(t.tier)}</span>
      <span class="muted">${t.phases.length} phases · ${t.loops.length} loop${t.loops.length === 1 ? '' : 's'}</span>
      <button type="button" class="linkish" id="na-change">change</button></p>
    <label class="fld" for="na-title"><span>Title</span>
      <input type="text" id="na-title" placeholder="e.g. Payments Service" autocomplete="off"></label>
    <label class="fld" for="na-repo" style="margin-top:.8rem"><span>Repository</span>
      <select id="na-repo"><option value="">loading…</option></select></label>
    <label class="fld" for="na-summary" style="margin-top:.8rem"><span>Summary <em>(optional)</em></span>
      <textarea class="editor" id="na-summary" spellcheck="false" style="height:5rem; white-space:pre-wrap"
        placeholder="what this architecture is for"></textarea></label>
    <label class="fld" style="margin-top:.8rem; flex-direction:row; align-items:center; gap:.5rem">
      <input type="checkbox" id="na-autorun" style="width:auto">
      <span style="text-transform:none; letter-spacing:0">Run an agent on the first phase right away</span></label>`;
  $('#na-change').addEventListener('click', openNewArchModal);
  $('#na-title').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); saveModal(); }
  });
  $('#na-title').focus();

  // The repo list comes from the same candidate scan that backs the Projects and
  // Code Map pickers, so what is offered here is exactly what the server accepts.
  try {
    newArch.repos = newArch.repos || (await api('/api/codemaps')).repos || [];
    $('#na-repo').innerHTML = newArch.repos
      .map(r => `<option value="${esc(r.path)}">${esc(r.name)}</option>`).join('')
      || '<option value="">no repositories found</option>';
  } catch (e) {
    $('#na-repo').innerHTML = '<option value="">could not load repositories</option>';
  }
}

async function saveModal() {
  if (!modalState) return;
  const btn = $('#modal-save');
  btn.disabled = true; $('#modal-status').textContent = 'Saving…';
  try {
    if (modalState.type === 'file') {
      await api('/api/content', { method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ path: modalState.path, content: $('#modal-editor').value }) });
    } else if (modalState.type === 'memory') {
      await api('/api/memory', { method: 'PATCH', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ id: modalState.id, text: $('#modal-editor').value }) });
      await loadMemory();
    } else if (modalState.type === 'new-memory') {
      const text = $('#modal-editor').value.trim();
      if (!text) throw new Error('Enter memory text');
      await api('/api/memory', { method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          text,
          scope: $('#modal-scope').value.trim(),
          agent: $('#modal-agent').value.trim(),
        }) });
      await loadMemory();
    } else if (modalState.type === 'new-file') {
      const name = $('#nf-name').value.trim();
      if (!name) throw new Error('Enter a name');
      const fsel = $('#nf-folder-sel');
      const folder = fsel
        ? (fsel.value === '__new__' ? $('#nf-folder-new').value.trim() : fsel.value)
        : '';
      await api('/api/create', { method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ kind: modalState.kind, name, folder, content: $('#modal-editor').value }) });
      ({ rule: loadRules, agent: loadAgents, skill: loadSkills }[modalState.kind])();
    } else if (modalState.type === 'new-mcp') {
      const name = $('#mcp-name').value.trim();
      const command = $('#mcp-cmd').value.trim();
      if (!name || !command) throw new Error('name and command are required');
      await api('/api/mcp', { method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ name, command, args: $('#mcp-args').value.trim() }) });
      loadMcp();
    } else if (modalState.type === 'approve-gate') {
      // The only path that advances a gate. The button opens this dialog; this
      // is where the decision becomes a write.
      await setArchPhase(modalState.id, modalState.order, modalState.target);
      setModal(false);
      return;
    } else if (modalState.type === 'new-arch') {
      if (modalState.step === 1) {                 // "Next", not "Create" yet
        if (!newArch.template) throw new Error('Pick a template');
        await newArchStep2();
        return;
      }
      const title = $('#na-title').value.trim();
      if (!title) throw new Error('Enter a title');
      const repo = $('#na-repo').value;
      if (!repo) throw new Error('Pick a repository');
      const created = await api('/api/architecture', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          repo,
          summary: $('#na-summary').value.trim(),
          template_id: newArch.template.id,
        }) });
      const autorun = $('#na-autorun') && $('#na-autorun').checked;
      setModal(false);                 // closed here, not in `finally`: a failed
      toast('Created ' + title);       // create keeps the form and its input intact
      await refreshArchIndex();        // so the card behind the detail view exists
      // Opt-in only: creating a plan and launching an agent at somebody's repo
      // are different decisions, and the second one should be deliberate.
      if (autorun) return startRun(created.id);
      return showArchDetail(created.id);
    }
    $('#modal-status').textContent = 'Saved';
    toast('Saved');
  } catch (e) {
    $('#modal-status').textContent = e.message;
    toast(e.message, true);
  } finally {
    // Step 1 of the wizard re-enables itself only when a template is selected.
    btn.disabled = modalState?.type === 'new-arch' && modalState.step === 1 && !newArch.template;
    if (['new-memory','new-file','new-mcp'].includes(modalState?.type)) setModal(false);
  }
}
$('#modal-close').addEventListener('click', () => setModal(false));
$('#modal-cancel').addEventListener('click', () => setModal(false));
$('#modal-save').addEventListener('click', saveModal);
$('#detail-modal').addEventListener('click', e => { if (e.target.id === 'detail-modal') setModal(false); });
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if ($('#phase-modal').classList.contains('open')) return closePhaseModal();
  setModal(false);
});

/* ---- Navigation ---- */
const loaded = {};
function show(view) {
  if (view !== 'architectures') stopRunTimers();   // no polling behind another panel
  if (view !== 'overview') stopOverviewPoll();
  $$('.nav-btn').forEach(b => b.setAttribute('aria-current', String(b.dataset.view === view)));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + view));
  location.hash = view;
  if (!loaded[view]) { loaded[view] = true; load(view); }
}
$$('.nav-btn').forEach(b => b.addEventListener('click', () => show(b.dataset.view)));

/* ---- Add buttons (human-only, via this dashboard) ---- */
$('#add-rule').addEventListener('click', () => openNewFileModal('rule'));
$('#add-agent').addEventListener('click', () => openNewFileModal('agent'));
$('#add-skill').addEventListener('click', () => openNewFileModal('skill'));
$('#add-mcp').addEventListener('click', openAddMcpModal);

/* The Overview answers "what is happening" — so it cannot be a snapshot either.
   Same discipline as the architectures list: one timer, torn down on leaving. */
let overviewTimer = null;
function stopOverviewPoll() {
  if (overviewTimer) { clearInterval(overviewTimer); overviewTimer = null; }
}
function startOverviewPoll() {
  stopOverviewPoll();
  overviewTimer = setInterval(() => {
    if ($('#view-overview').classList.contains('active')) loadOverview({ quiet: true });
    else stopOverviewPoll();
  }, RUN_POLL_RUNNING);
}

function load(view) {
  ({ overview: loadOverview, assistant: loadAssistant, memory: loadMemory, skills: loadSkills,
     agents: loadAgents, mcp: loadMcp, rules: loadRules,
     hooks: loadHooks, projects: loadProjects, codemap: loadCodemap,
     architectures: loadArchitectures, harness: loadHarness }[view] || (()=>{}))();
}

/* ---- Overview ---- */
async function loadOverview(opts = {}) {
  const el = $('#tiles');
  const detail = $('#overview-detail');
  try {
    if (!opts.quiet) startOverviewPoll();
    const [h, mcp, rules, archList] = await Promise.all([
      api('/api/health'),
      api('/api/mcp').catch(e => ({ servers: [], count: 0, errors: [e.message] })),
      api('/api/rules').catch(e => ({ groups: {}, count: 0, errors: [e.message] })),
      api('/api/architectures').catch(() => ({ results: [] })),
    ]);
    const architectures = (archList.results || []).map(normalizeArch);
    // 'unused' is a healthy answer, not a degraded one: the container is not
    // how Qdrant is served on this machine.
    const stat = s => (s === 'up' || s === 'unused') ? 'ok' : (s === 'unknown' ? 'warn' : 'bad');
    const tiles = [
      { label: 'Ollama', c: stat(h.ollama.status), dot: stat(h.ollama.status),
        value: h.ollama.status === 'up' ? (h.ollama.count + ' models') : 'DOWN',
        sub: h.ollama.status === 'up' ? (h.ollama.models||[]).slice(0,2).join(', ') : (h.ollama.error||'') },
      { label: 'Qdrant', c: stat(h.qdrant.status), dot: stat(h.qdrant.status),
        value: h.qdrant.status === 'up' ? (h.qdrant.points ?? 0) + ' pts' : 'DOWN',
        sub: h.qdrant.status === 'up'
          ? (h.qdrant.transport ? h.qdrant.transport + ' · ' : '') + 'collection ' + (h.qdrant.state||'')
          : (h.qdrant.error||'') },
      { label: 'Docker', c: stat(h.docker.status), dot: stat(h.docker.status),
        value: h.docker.status === 'up' ? 'RUNNING'
             : (h.docker.status === 'unused' ? 'NOT IN USE' : 'STOPPED'),
        sub: h.docker.detail || h.docker.error || '' },
      { label: 'Skills', c: 'ok', value: h.skills_count ?? '—', sub: 'SKILL.md installed' },
      { label: 'Agents', c: 'ok', value: h.agents_count ?? '—', sub: 'sub-agents' },
      { label: 'MCP', c: (mcp.errors||[]).length ? 'warn' : 'ok', value: mcp.count ?? h.mcp_count ?? '—', sub: 'configured servers' },
      { label: 'Rules', c: 'ok', value: h.rules_count ?? rules.count ?? '—', sub: 'instruction files' },
      // null when the store could not be read — '—' rather than a printed "null".
      { label: 'Architectures', c: 'ok', value: h.architectures_count ?? '—', sub: 'feature workflows' },
    ];
    el.innerHTML = tiles.map(t => `
      <article class="tile st-${t.c}">
        <div class="label">${t.dot?`<span class="dot ${t.dot}"></span>`:''}${esc(t.label)}</div>
        <div class="value">${esc(t.value)}</div>
        <div class="sub">${esc(t.sub)}</div>
      </article>`).join('');
    // memory stats tile (async, may be slow)
    const memTile = document.createElement('article');
    memTile.className = 'tile st-ok';
    memTile.innerHTML = `<div class="label">Memories</div><div class="value"><span class="spinner"></span></div><div class="sub">counting…</div>`;
    el.appendChild(memTile);
    try {
      const m = await api('/api/memories');
      const nScopes = Object.keys(m.scopes||{}).length;
      const shared = (m.scopes||{})[SHARED] || 0;
      memTile.innerHTML = `<div class="label">Memories</div><div class="value">${m.total}</div>
        <div class="sub">${nScopes} scopes · ${shared} in 🧠</div>`;
      renderOverviewDetail(h, m, mcp, rules, architectures);
    } catch(e) {
      memTile.className = 'tile st-warn';
      memTile.innerHTML = `<div class="label">Memories</div><div class="value">—</div><div class="sub">${esc(e.message)}</div>`;
      detail.innerHTML = `<div class="empty">Memory detail failed: ${esc(e.message)}</div>`;
    }
  } catch (e) {
    el.innerHTML = `<div class="empty">Health check failed: ${esc(e.message)}</div>`;
    detail.innerHTML = '';
  }
}

function renderKVs(rows) {
  return rows.map(([k, v]) => `<div class="panel-row"><div class="k">${esc(k)}</div><div class="v">${esc(v ?? '—')}</div></div>`).join('');
}
function renderScopeBars(scopes) {
  const entries = Object.entries(scopes || {}).sort((a,b) => b[1] - a[1]);
  if (!entries.length) return '<div class="empty">No memory scopes yet.</div>';
  const max = Math.max(...entries.map(([,n]) => n), 1);
  return `<div class="scope-bars">${entries.map(([name, n]) => `
    <div class="scope-line">
      <div class="scope-name">${esc(scopeLabel(name))}</div>
      <div class="bar"><span style="--w:${Math.max(8, Math.round((n / max) * 100))}%"></span></div>
      <div class="scope-num">${n}</div>
    </div>`).join('')}</div>`;
}
function renderMiniMemories(items) {
  const recent = (items || []).slice(0, 5);
  if (!recent.length) return '<div class="empty">No recent memories.</div>';
  return `<div class="mini-list">${recent.map(it => `
    <div class="mini-item" data-id="${esc(it.id)}" data-detail="memory">
      <strong>${esc(it.memory || '')}</strong>
      <span>${esc(scopeLabel(it.run_id || '(none)'))} · ${esc(it.agent_id || '(none)')} · ${esc(fmtTime(it.created_at))}</span>
    </div>`).join('')}</div>`;
}
/* What is actually moving right now, so the first screen answers the first
   question. Anything at rest is left to its own panel. */
function renderInFlight(architectures) {
  const rows = (architectures || []).filter(a =>
    a.runStatus === 'running' || (a.tasksTotal > 0 && a.tasksDone < a.tasksTotal));
  if (!rows.length) {
    return '<div class="empty-note">Nothing running. Start a phase from Architectures.</div>';
  }
  return `<div class="flight-list">${rows.map(a => `
    <button type="button" class="flight-row" data-arch="${esc(a.id)}">
      <span class="flight-dot${a.runStatus === 'running' ? ' live' : ''}"></span>
      <span class="flight-name">${esc(a.title)}</span>
      <span class="flight-phase">${esc(a.currentPhase || '—')}</span>
      <span class="flight-tasks">${a.tasksDone}/${a.tasksTotal} tasks</span>
      <span class="flight-bar"><i style="--w:${a.progress}%"></i></span>
      <span class="flight-pct">${a.progress}%</span>
    </button>`).join('')}</div>`;
}

function renderOverviewDetail(h, memories, mcp, rules, architectures) {
  const detail = $('#overview-detail');
  memoryById = new Map((memories.results || []).map(i => [String(i.id), i]));
  detail.innerHTML = `
    <div class="overview-grid">
      <section class="overview-panel wide" id="overview-live">
        <h3>In flight</h3>
        ${renderInFlight(architectures)}
      </section>
      <section class="overview-panel">
        <h3>Memory Scopes</h3>
        ${renderScopeBars(memories.scopes)}
      </section>
      <section class="overview-panel wide">
        <h3>Recent Memories</h3>
        ${renderMiniMemories(memories.results)}
        <div class="quick-actions">
          <button class="btn" data-jump="memory">Open Memory</button>
          <button class="btn" data-jump="skills">Browse Skills</button>
          <button class="btn" data-jump="agents">Inspect Agents</button>
          <button class="btn" data-jump="mcp">Check MCP</button>
          <button class="btn" data-jump="rules">Edit Rules</button>
        </div>
      </section>
    </div>`;
  $$('#overview-detail [data-detail="memory"]').forEach(el => el.addEventListener('click', () => openMemoryModal(el.dataset.id)));
  $$('#overview-detail [data-jump]').forEach(btn => btn.addEventListener('click', () => show(btn.dataset.jump)));
  // A row is a shortcut into the thing it describes.
  $$('#overview-detail .flight-row').forEach(row => row.addEventListener('click', () => {
    show('architectures');
    showArchDetail(row.dataset.arch);
  }));
}

/* ---- Memory ---- */
let lastFilters = { scope: '', agent: '' };
function memCard(it, opts={}) {
  const scope = it.run_id || '(none)';
  const shared = isShared(scope);
  const isSuperseded = (it.status === 'superseded') || ((it.metadata || {}).status === 'superseded');
  const score = (opts.search && it.score != null) ? `<span class="badge score">◎ ${Number(it.score).toFixed(2)}</span>` : '';
  const supersededBadge = isSuperseded ? `<span class="badge superseded" style="background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.3)">⚠️ SUPERSEDED</span>` : '';
  const cardStyle = isSuperseded ? 'style="opacity:0.72;border-left:3px solid #ef4444;"' : '';
  return `<article class="card ${shared?'shared':''} ${isSuperseded?'superseded-card':''}" data-id="${esc(it.id)}" data-detail="memory" ${cardStyle}>
    <button class="del" title="Delete memory" aria-label="Delete memory">✕</button>
    <p class="text">${esc(it.memory)}</p>
    <div class="meta">
      <span class="badge scope ${shared?'shared':''}">${esc(scopeLabel(scope))}</span>
      <span class="badge agent">◇ ${esc(it.agent_id||'(none)')}</span>
      <span class="badge time">${esc(fmtTime(it.created_at))}</span>
      ${supersededBadge}
      ${score}
    </div></article>`;
}
function renderGrouped(items) {
  const body = $('#mem-body');
  memoryById = new Map(items.map(i => [String(i.id), i]));
  if (!items.length) { body.innerHTML = '<div class="empty">No memories match.</div>'; bindMemoryCards(); return; }
  const shared = items.filter(i => isShared(i.run_id));
  const proj = {};
  items.filter(i => !isShared(i.run_id)).forEach(i => {
    const s = i.run_id || '(none)'; (proj[s] = proj[s] || []).push(i);
  });
  let html = '';
  if (shared.length) {
    html += `<div class="mem-group-title" style="color:var(--brain)">🧠 Shared Brain (global) <span class="count">${shared.length}</span></div>`;
    html += '<div class="cards">' + shared.map(memCard).join('') + '</div>';
  }
  Object.keys(proj).sort().forEach(s => {
    html += `<div class="mem-group-title">📁 ${esc(s)} <span class="count">${proj[s].length}</span></div>`;
    html += '<div class="cards">' + proj[s].map(memCard).join('') + '</div>';
  });
  body.innerHTML = html;
  bindMemoryCards();
}
function bindMemoryCards() {
  $$('#mem-body .card').forEach(card => card.addEventListener('click', () => openMemoryModal(card.dataset.id)));
  $$('#mem-body .del').forEach(btn => btn.addEventListener('click', async (event) => {
    event.stopPropagation();
    const card = btn.closest('.card'); const id = card.dataset.id;
    if (!confirm('Delete this memory permanently?')) return;
    btn.disabled = true;
    try { await api('/api/memory?id=' + encodeURIComponent(id), { method: 'DELETE' });
      card.style.opacity = '0'; card.style.transform = 'scale(0.96)';
      setTimeout(() => loadMemory(), 250); toast('Memory deleted');
    } catch(e) { toast(e.message, true); btn.disabled = false; }
  }));
}
async function loadMemory() {
  const body = $('#mem-body');
  body.innerHTML = '<div class="loading"><span class="spinner"></span>Loading memories… (local LLM, a few seconds)</div>';
  try {
    const scope = $('#mem-scope').value, agent = $('#mem-agent').value;
    const q = new URLSearchParams(); if (scope) q.set('scope', scope); if (agent) q.set('agent', agent);
    const data = await api('/api/memories?' + q.toString());
    // populate filters (from full unfiltered counts)
    fillFilter('#mem-scope', data.scopes, scope, scopeLabel);
    fillFilter('#mem-agent', data.agents, agent, x => '◇ ' + x);
    $('#mem-count').textContent = data.results.length + ' shown · ' + data.total + ' total';
    renderGrouped(data.results);
  } catch(e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}
function fillFilter(sel, counts, current, labeler) {
  const el = $(sel); if (!counts) return;
  const first = el.querySelector('option').outerHTML;
  const opts = Object.keys(counts).sort((a,b)=>counts[b]-counts[a])
    .map(k => `<option value="${esc(k)}" ${k===current?'selected':''}>${esc(labeler(k))} (${counts[k]})</option>`).join('');
  el.innerHTML = first + opts;
}
async function runSearch() {
  const q = $('#mem-search').value.trim();
  if (!q) return loadMemory();
  const body = $('#mem-body');
  body.innerHTML = '<div class="loading"><span class="spinner"></span>Semantic search running…</div>';
  try {
    const scope = $('#mem-scope').value, agent = $('#mem-agent').value;
    const p = new URLSearchParams({ q }); if (scope) p.set('scope', scope); if (agent) p.set('agent', agent);
    const data = await api('/api/search?' + p.toString());
    memoryById = new Map(data.results.map(i => [String(i.id), i]));
    $('#mem-count').textContent = data.results.length + ' results for “' + q + '”' + (data.layered ? ' · includes 🧠 shared' : '');
    body.innerHTML = data.results.length
      ? '<div class="cards">' + data.results.map(i => memCard(i, {search:true})).join('') + '</div>'
      : '<div class="empty">No semantic matches.</div>';
    bindMemoryCards();
  } catch(e) { body.innerHTML = `<div class="empty">Search failed: ${esc(e.message)}</div>`; }
}
$('#mem-search').addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });
$('#mem-refresh').addEventListener('click', () => { $('#mem-search').value=''; loadMemory(); });
$('#mem-scope').addEventListener('change', () => $('#mem-search').value ? runSearch() : loadMemory());
$('#mem-agent').addEventListener('change', () => $('#mem-search').value ? runSearch() : loadMemory());
$('#mem-add-open').addEventListener('click', openAddMemoryModal);

/* ---- Skills ---- */
let skillTimer;
function usageTopLine(top) {
  if (!top || !top.length) return '';
  const items = top.slice(0, 6).map(t =>
    `<span class="ut-item">${esc(t.name)} <b>${t.count}×</b></span>`).join(' · ');
  return `<div class="usage-top"><span class="ut-label">🔥 Most used</span>${items}</div>`;
}
async function loadSkills() {
  const body = $('#skill-body');
  try {
    const q = $('#skill-search').value.trim();
    const [data, usage] = await Promise.all([
      api('/api/skills' + (q ? '?q=' + encodeURIComponent(q) : '')),
      api('/api/usage').catch(() => ({ skills: {}, top_skills: [] })),
    ]);
    const counts = usage.skills || {};
    $('#skill-count').textContent = data.results.length + ' skills';
    body.innerHTML = usageTopLine(usage.top_skills) + (data.results.length ? '<div class="list">' + data.results.map(s => {
      const n = counts[s.name] || 0;
      return `<article class="item" data-path="${esc(s.path)}" data-title="${esc(s.name)}" data-kind="Skill">${s.source.endsWith('/skills') ? delBtn(s.path, 'skilldir', s.name) : ''}<div class="name">${esc(s.name)}${n ? `<span class="use-badge">${n}×</span>` : ''}<span class="src">${esc(s.source)}</span></div>
      <div class="desc">${esc(s.description) || '<span class="muted">no description</span>'}</div></article>`;
    }).join('') + '</div>' : '<div class="empty">No skills match.</div>');
    bindFileItems('#skill-body', 'Skill');
    bindDeletes('#skill-body', loadSkills);
  } catch(e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}
$('#skill-search').addEventListener('input', () => { clearTimeout(skillTimer); skillTimer = setTimeout(loadSkills, 220); });

/* ---- Agents ---- */
async function loadAgents() {
  const body = $('#agent-body');
  try {
    const [data, usage] = await Promise.all([
      api('/api/agents'),
      api('/api/usage').catch(() => ({ agents: {}, top_agents: [] })),
    ]);
    const counts = usage.agents || {};
    body.innerHTML = usageTopLine(usage.top_agents) + (data.results.length ? '<div class="list">' + data.results.map(a => {
      // The server attributes each count to one card; counts[] is the fallback.
      const n = a.count ?? counts[a.key ?? a.name] ?? 0;
      // Only files under the managed ~/.claude|.codex/agents roots are deletable;
      // plugin-shipped and built-in agents are not ours to remove.
      const del = (a.source || '').endsWith('/agents') ? delBtn(a.path, 'file', a.name) : '';
      return `<article class="item" data-path="${esc(a.path)}" data-title="${esc(a.name)}" data-kind="Agent">${del}<div class="name">⌘ ${esc(a.name)}${n ? `<span class="use-badge">${n}×</span>` : ''}<span class="src">${esc(a.source || 'local')}</span></div>
      <div class="desc">${esc(a.description) || '<span class="muted">no description</span>'}</div></article>`;
    }).join('') + '</div>' : '<div class="empty">No agents found.</div>');
    bindFileItems('#agent-body', 'Agent');
    bindDeletes('#agent-body', loadAgents);
  } catch(e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}

/* ---- MCP ---- */
async function loadMcp() {
  const body = $('#mcp-body');
  try {
    const data = await api('/api/mcp');
    body.innerHTML = (data.servers||[]).length ? '<div class="list">' + data.servers.map(s => `
      <article class="item" data-path="${esc(s.path || '')}" data-title="${esc(s.name)}" data-kind="MCP config">${s.path ? delBtn(s.path, 'mcp', s.name) : ''}<div class="name">⇄ ${esc(s.name)}<span class="src">${esc(s.origin)}</span></div>
      <div class="desc" style="font-family:var(--mono);font-size:0.78rem;-webkit-line-clamp:4">${esc(s.command) || esc(s.type)}</div></article>`).join('') + '</div>'
      : '<div class="empty">No MCP servers configured.</div>';
    bindFileItems('#mcp-body', 'MCP config');
    bindDeletes('#mcp-body', loadMcp);
  } catch(e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}

/* ---- Rules ---- */
async function loadRules() {
  const body = $('#rules-body');
  try {
    const [data, instr] = await Promise.all([
      api('/api/rules'),
      api('/api/instructions').catch(() => ({ items: [] })),
    ]);
    const groups = data.groups || {};
    const keys = Object.keys(groups).sort();
    const items = instr.items || [];
    const instrBlock = items.length ? `
      <div class="rule-group"><h3>📄 CLAUDE.md · Instructions</h3>
      <div class="rule-files">${items.map(i => `<span class="chip" data-path="${esc(i.path)}" data-title="${esc(i.name)}" data-kind="Instructions">${esc(i.name)}</span>`).join('')}</div></div>` : '';
    const rulesHtml = keys.length ? keys.map(g => `
      <div class="rule-group"><h3>${esc(g)}</h3>
      <div class="rule-files">${groups[g].map(f => `<span class="chip" data-path="${esc(f.path)}" data-title="${esc(f.name)}" data-kind="Rule">${esc(f.name)}${delBtn(f.path, 'file', f.name)}</span>`).join('')}</div></div>`).join('') : '';
    body.innerHTML = (instrBlock + rulesHtml) || '<div class="empty">No rules found.</div>';
    bindFileItems('#rules-body', 'Rule');
    bindDeletes('#rules-body', loadRules);
  } catch(e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}

/* ---- Hooks ---- */
const EVENT_BADGE = { PreToolUse: 'push', PostToolUse: 'clean', SessionStart: 'behind',
  SessionEnd: 'behind', Stop: 'dirty', UserPromptSubmit: 'push' };
async function loadHooks() {
  const body = $('#hooks-body');
  try {
    const data = await api('/api/hooks');
    const groups = data.groups || {};
    const keys = Object.keys(groups);
    if (!keys.length) { body.innerHTML = '<div class="empty">No hooks configured.</div>'; return; }
    body.innerHTML = keys.map(ev => `
      <div class="rule-group"><h3><span class="pbadge ${EVENT_BADGE[ev] || 'nogit'}">${esc(ev)}</span> <span class="muted">${groups[ev].length} hook${groups[ev].length > 1 ? 's' : ''}</span></h3>
      <div class="timeline">${groups[ev].map(h => `
        <div class="tl-row">${h.matcher ? `<span class="hk-match">${esc(h.matcher)}</span>` : ''}<code class="hk-cmd">${esc(h.command)}</code><span class="muted"> · ${esc(h.source)}${h.async ? ' · async' : ''}${h.timeout ? ' · ' + h.timeout + 's' : ''}</span></div>`).join('')}</div></div>`).join('');
  } catch(e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}

function bindFileItems(rootSel, fallbackKind) {
  $$(rootSel + ' [data-path]').forEach(el => el.addEventListener('click', () => {
    const path = el.dataset.path;
    if (!path) return toast('No editable config path for this item', true);
    openFileModal(path, `${el.dataset.kind || fallbackKind}: ${el.dataset.title || el.textContent.trim()}`);
  }));
}

/* ---- Projects ---- */
function relTime(epoch) {
  if (!epoch) return '';
  const s = Date.now() / 1000 - epoch;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}
function projBadges(p) {
  if (!p.is_git) return '<span class="pbadge nogit">no git</span>';
  const b = [p.dirty ? `<span class="pbadge dirty">${p.dirty} uncommitted</span>`
                     : '<span class="pbadge clean">clean</span>'];
  if (p.ahead) b.push(`<span class="pbadge push">${p.ahead} unpushed</span>`);
  if (p.behind) b.push(`<span class="pbadge behind">${p.behind} behind</span>`);
  return b.join('');
}
const ACT_VERB = { commit: 'committed', push: 'pushed', session: 'worked' };
const ACT_BADGE = { commit: 'clean', push: 'push', session: 'behind' };
function activityLine(p) {
  if (p.last_activity) {
    const a = p.last_activity;
    return `<span class="pact">⚡ ${esc(a.agent || '?')} ${esc(ACT_VERB[a.action] || a.action || '')} — ${esc(a.detail || '')}</span>`;
  }
  if (p.last_session) return `<span class="pact">🧠 last Claude session ${relTime(p.last_session)}</span>`;
  return '';
}
async function loadProjects() {
  const body = $('#proj-body');
  body.innerHTML = '<div class="loading"><span class="spinner"></span>Scanning projects…</div>';
  try {
    const data = await api('/api/projects');
    if (data.error) { body.innerHTML = `<div class="empty">${esc(data.error)}</div>`; return; }
    const list = data.projects || [];
    body.innerHTML = '<div class="proj-grid">' + list.map(p => {
      const lc = p.last_commit;
      return `<article class="proj" data-path="${esc(p.path)}">
        <div class="proj-top"><span class="proj-name">${esc(p.name)}</span><span class="pbadges">${projBadges(p)}</span></div>
        ${lc ? `<div class="proj-commit"><span class="phash">${esc(lc.hash)}</span> ${esc(lc.subject)} <span class="muted">· ${esc(lc.rel)}</span></div>`
             : '<div class="proj-commit muted">no commits</div>'}
        <div class="proj-foot">${p.is_git && p.branch ? `<span class="pbranch">⎇ ${esc(p.branch)}</span>` : ''}${activityLine(p)}</div>
      </article>`;
    }).join('') + '</div>';
    $$('#proj-body .proj').forEach(el =>
      el.addEventListener('click', () => openProjectModal(el.dataset.path)));
  } catch (e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}
/* ---- Code Map ---- */
/* Set by renderCodemap before anything draws — cmOpen needs the repo root to
   absolutise graph.json's repo-relative paths, and the scheme the server says
   this machine's editor answers on. */
let cmRepo = '', cmEditor = '';

/* Open a symbol where it actually lives. graph.json stores a repo-relative
   path and an "L928"-style location; editors want an absolute path and a bare
   line number. Nothing happens if no app is registered for the scheme, so the
   toast is the only feedback the browser can give us. */
function cmOpen(file, loc) {
  if (!file || !cmRepo || !cmEditor) return;
  const abs = cmRepo.replace(/\/+$/, '') + '/' + String(file).replace(/^\/+/, '');
  const line = (String(loc || '').match(/\d+/) || ['1'])[0];
  const ref = loc ? (file + ':' + loc) : file;
  window.location.href = cmEditor.replace('{path}', encodeURI(abs)).replace('{line}', line);
  toast('Opening ' + ref);
}

const cmLoc = (file, loc) => {
  if (!file) return '';
  const ref = loc ? (file + ':' + loc) : file;
  return `<button class="cm-loc" data-file="${esc(file)}" data-loc="${esc(loc || '')}"
    data-copy="${esc(ref)}" title="Open ${esc(ref)} in your editor — ⌥click to copy the path"
    >${esc(ref)} ↗</button>`;
};

function cmBadge(f) {
  if (!f || f.state === 'unknown') return '';
  const text = {
    fresh: 'up to date with HEAD',
    stale: (f.behind != null ? f.behind + ' commits behind' : 'behind HEAD'),
    diverged: 'built on a different branch or rewritten history',
    unknown_commit: 'built from a commit not in this repo',
  }[f.state] || f.state;
  return `<span class="cm-badge ${esc(f.state)}">${esc(text)}</span>`;
}

async function loadCodemap() {
  const sel = $('#cm-repo');
  const body = $('#cm-body');
  try {
    const data = await api('/api/codemaps');
    const repos = data.repos || [];
    if (!repos.length) { body.innerHTML = '<div class="empty">No repos found.</div>'; return; }
    sel.innerHTML = repos.map(r =>
      `<option value="${esc(r.path)}"${r.has_graph ? '' : ' disabled'}>${esc(r.name)}${r.has_graph ? '' : ' — no map'}</option>`
    ).join('');
    const first = repos.find(r => r.has_graph);
    if (!first) {
      body.innerHTML = `<div class="empty">No repo has a code map yet. Run <code>graphify extract . --code-only</code> in one.</div>`;
      return;
    }
    sel.value = first.path;
    sel.onchange = () => renderCodemap(sel.value);
    $('#cm-refresh').onclick = () => renderCodemap(sel.value);
    renderCodemap(first.path);
  } catch (e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}

async function renderCodemap(path) {
  const body = $('#cm-body');
  body.innerHTML = '<div class="loading"><span class="spinner"></span>Reading graph…</div>';
  try {
    const d = await api('/api/codemap?path=' + encodeURIComponent(path));

    if (d.missing) {
      body.innerHTML = `<div class="empty">No code map for <b>${esc(d.name)}</b> yet.<br><br>
        Run <code>${esc(d.cmd)}</code> in <code>${esc(d.repo)}</code>, then reload.
        <br><br><button class="cm-loc" data-copy="${esc(d.cmd)}">${esc(d.cmd)} ⧉</button></div>`;
      wireRefs();
      return;
    }

    const c = d.counts;
    cmRepo = d.repo || '';
    cmEditor = d.editor_url || '';
    const clusters = d.communities || [];
    const maxDeg = Math.max(1, ...(d.gods || []).map(g => g.degree || 0));

    const legend = clusters.map((cl, i) => `<button class="cm-chip" data-group="${i}" aria-pressed="false">
        <span class="dot" style="background:${cmColor(i)}"></span>${esc(cl.name)}<span class="n">${esc(cl.size)}</span>
      </button>`).join('');

    const gods = (d.gods || []).map(g => {
      const gi = clusters.findIndex(cl => cl.name === g.community_name);
      const ci = gi < 0 ? 0 : gi;
      return `<div class="cm-row">
        <span class="dot" style="background:${cmColor(ci)}"></span>
        <span class="nm">${esc(g.label)}</span>
        <span class="cm-bar"><i style="width:${Math.round((g.degree / maxDeg) * 100)}%;background:${cmColor(ci)}"></i></span>
        ${cmLoc(g.source_file, g.source_location)}
        <span class="cm-deg">${esc(g.degree)}</span>
      </div>`;
    }).join('') || '<div class="muted">none</div>';

    const surprises = (d.surprises || []).map(s => `<div class="cm-surprise">
        ${esc(s.source)} <span class="cm-arrow">→</span> ${esc(s.target)}
        <span class="why">${esc(s.why || s.relation || '')}</span>
      </div>`).join('') || '<div class="muted">none found</div>';

    body.innerHTML = `
      <div class="cm-stats">
        <span class="cm-stat"><b>${esc(c.nodes)}</b> symbols</span>
        <span class="cm-stat"><b>${esc(c.links)}</b> connections</span>
        <span class="cm-stat"><b>${esc(c.communities)}</b> clusters</span>
        ${cmBadge(d.freshness)}
      </div>
      ${d.analysis_warning ? `<div class="cm-warn">${esc(d.analysis_warning)}</div>` : ''}
      <div class="cm-legend">${legend}</div>
      <div class="cm-stage">
        <canvas class="cm-canvas" id="cm-canvas"></canvas>
        <div class="cm-hint">drag to pan · scroll to zoom · click a dot to open it in your editor${d.graph && d.graph.truncated ? ' · showing the ' + esc(d.graph.nodes.length) + ' most connected of ' + esc(c.nodes) : ''}</div>
        <div class="cm-tip" id="cm-tip"></div>
      </div>
      <div class="cm-cols">
        <div class="cm-panel">
          <h3>Most connected</h3>
          <p class="sub">The symbols the most other code touches — ranked by ${esc(d.gods_source)}.</p>
          ${gods}
        </div>
        <div class="cm-panel">
          <h3>Unexpected links</h3>
          <p class="sub">Connections that cross cluster or directory boundaries.</p>
          ${surprises}
        </div>
      </div>
    `;
    wireRefs();
    cmDrawGraph(d.graph, clusters);
  } catch (e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}

/* Cluster palette — spread around the oklch hue wheel, matching the panel's tokens. */
const CM_HUES = [200, 90, 155, 300, 45, 260, 20, 175, 320, 120, 65, 230];
const cmColor = i => `oklch(74% 0.15 ${CM_HUES[i % CM_HUES.length]})`;

/* Force-directed layout drawn on a canvas. No external library: the panel is
   offline-only, so the simulation is a plain spring/repulsion loop. */
function cmDrawGraph(g, clusters) {
  const canvas = $('#cm-canvas');
  if (!canvas || !g || !g.nodes.length) return;
  const tip = $('#cm-tip');
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  let W = 0, H = 0;

  function size() {
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  size();

  const byId = new Map();
  const nodes = g.nodes.map((n, i) => {
    const a = (i / g.nodes.length) * Math.PI * 2;
    const o = { ...n, x: W / 2 + Math.cos(a) * 140 + (i % 7) * 3,
                      y: H / 2 + Math.sin(a) * 140 + (i % 5) * 3, vx: 0, vy: 0,
                r: 3 + Math.min(9, Math.sqrt(n.degree || 1) * 2.2) };
    byId.set(n.id, o);
    return o;
  });
  const links = g.links.map(l => ({ s: byId.get(l.source), t: byId.get(l.target) }))
                       .filter(l => l.s && l.t);

  let view = { x: 0, y: 0, k: 1 }, hot = null, only = null, alpha = 1;

  function step() {
    const kRep = 1400, kSpr = 0.012, len = 46;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const d2 = dx * dx + dy * dy || 0.01;
        if (d2 > 90000) continue;            // ignore far pairs — keeps 600 nodes cheap
        const f = kRep / d2, d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
    }
    for (const l of links) {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.hypot(dx, dy) || 0.01;
      const f = (d - len) * kSpr;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;
    }
    for (const n of nodes) {
      n.vx += (W / 2 - n.x) * 0.0016; n.vy += (H / 2 - n.y) * 0.0016;   // gentle centring
      n.vx *= 0.86; n.vy *= 0.86;
      n.x += n.vx * alpha; n.y += n.vy * alpha;
    }
    alpha = Math.max(0.06, alpha * 0.995);
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(view.x, view.y); ctx.scale(view.k, view.k);
    ctx.lineWidth = 1;
    for (const l of links) {
      const dim = only != null && l.s.group !== only && l.t.group !== only;
      ctx.strokeStyle = dim ? 'oklch(30% 0.02 265 / 0.22)' : 'oklch(48% 0.03 265 / 0.5)';
      ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
    }
    for (const n of nodes) {
      const dim = only != null && n.group !== only;
      ctx.globalAlpha = dim ? 0.18 : 1;
      ctx.fillStyle = cmColor(n.group);
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); ctx.fill();
      if (n === hot) {
        ctx.globalAlpha = 1; ctx.strokeStyle = 'oklch(96% 0.01 265)'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 3, 0, Math.PI * 2); ctx.stroke(); ctx.lineWidth = 1;
      }
      // Label the hubs only — labelling all 114 makes the picture unreadable.
      if (!dim && (n.r > 7 || n === hot)) {
        ctx.globalAlpha = 0.85;
        ctx.fillStyle = 'oklch(88% 0.01 265)';
        ctx.font = '11px ui-monospace, monospace';
        ctx.fillText(n.label, n.x + n.r + 4, n.y + 3);
      }
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  let raf;
  (function frame() { step(); draw(); raf = requestAnimationFrame(frame); })();

  // Stop the loop and drop the window listeners once the view is replaced, so
  // re-rendering (repo switch, Refresh) never leaves a simulation or a resize
  // handler bound to a dead canvas.
  const life = new AbortController();
  const watch = new MutationObserver(() => {
    if (!document.getElementById('cm-canvas')) {
      cancelAnimationFrame(raf); life.abort(); watch.disconnect();
    }
  });
  watch.observe($('#cm-body'), { childList: true, subtree: true });

  const at = e => {
    const b = canvas.getBoundingClientRect();
    return { x: (e.clientX - b.left - view.x) / view.k, y: (e.clientY - b.top - view.y) / view.k };
  };
  const pick = p => nodes.find(n => Math.hypot(n.x - p.x, n.y - p.y) <= n.r + 4);
  const refOf = n => n.source_file + (n.source_location ? ':' + n.source_location : '');

  let drag = null;
  canvas.addEventListener('mousedown', e => { drag = { x: e.clientX - view.x, y: e.clientY - view.y }; });
  window.addEventListener('mouseup', () => { drag = null; }, { signal: life.signal });
  canvas.addEventListener('mousemove', e => {
    if (drag) { view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; return; }
    const n = pick(at(e));
    hot = n || null;
    if (n) {
      const b = canvas.getBoundingClientRect();
      tip.innerHTML = `<b>${esc(n.label)}</b><br><span class="t-loc">${esc(n.source_file || 'no file')}${n.source_location ? ':' + esc(n.source_location) : ''}</span>`;
      tip.style.left = Math.min(e.clientX - b.left + 12, b.width - 260) + 'px';
      tip.style.top = (e.clientY - b.top + 12) + 'px';
      tip.classList.add('on');
    } else { tip.classList.remove('on'); }
  });
  canvas.addEventListener('mouseleave', () => { tip.classList.remove('on'); hot = null; });
  canvas.addEventListener('wheel', e => {
    e.preventDefault();
    const b = canvas.getBoundingClientRect(), mx = e.clientX - b.left, my = e.clientY - b.top;
    const k = Math.min(4, Math.max(0.3, view.k * (e.deltaY < 0 ? 1.1 : 0.9)));
    view.x = mx - (mx - view.x) * (k / view.k); view.y = my - (my - view.y) * (k / view.k); view.k = k;
  }, { passive: false });
  canvas.addEventListener('click', e => {
    const n = pick(at(e));
    if (!n || !n.source_file) return;
    if (!e.altKey) { cmOpen(n.source_file, n.source_location); return; }
    navigator.clipboard.writeText(refOf(n)).then(
      () => toast('Copied ' + refOf(n)), () => toast('Copy failed', true));
  });

  $$('#cm-body .cm-chip').forEach(chip => chip.addEventListener('click', () => {
    const group = Number(chip.dataset.group);
    only = only === group ? null : group;
    alpha = Math.max(alpha, 0.35);
    $$('#cm-body .cm-chip').forEach(x => {
      const on = only != null && Number(x.dataset.group) === only;
      x.setAttribute('aria-pressed', String(on));
      x.classList.toggle('muted-chip', only != null && !on);
    });
  }));

  window.addEventListener('resize', size, { signal: life.signal });
}

function wireRefs() {
  $$('#cm-body .cm-loc').forEach(btn => btn.addEventListener('click', async e => {
    if (!e.altKey) { cmOpen(btn.dataset.file, btn.dataset.loc); return; }
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      toast('Copied ' + btn.dataset.copy);
    } catch (err) { toast('Copy failed', true); }
  }));
}
/* ---- end codemap ---- */
async function openProjectModal(path) {
  openModal({ title: 'Project', meta: path, content: '', editable: false, state: null });
  const body = $('#modal-body');
  body.innerHTML = '<div class="loading"><span class="spinner"></span>Loading…</div>';
  try {
    const p = await api('/api/project?path=' + encodeURIComponent(path));
    $('#modal-title').textContent = p.name;
    $('#modal-meta').textContent = p.path;
    const commits = (p.commits || []).map(c =>
      `<div class="tl-row"><span class="phash">${esc(c.hash)}</span> ${esc(c.subject)} <span class="muted">· ${esc(c.author)} · ${esc(c.rel)}</span></div>`
    ).join('') || '<div class="muted">no commits</div>';
    const acts = (p.activity || []).map(a =>
      `<div class="tl-row"><span class="pbadge ${ACT_BADGE[a.action] || 'nogit'}">${esc(a.action)}</span> <b>${esc(a.agent)}</b> ${esc(a.detail || '')} <span class="muted">· ${a.session ? esc(String(a.session).slice(0, 8)) + ' · ' : ''}${esc(a.ts || '')}</span></div>`
    ).join('') || '<div class="muted">No logged agent activity yet — appears here once agents commit/push or finish a session in this project.</div>';
    body.innerHTML = `<div class="pdetail">
      <div class="pbadges">${projBadges(p)}${p.remote ? `<span class="pbadge nogit">${esc(p.remote.replace(/^https?:\/\//, ''))}</span>` : ''}</div>
      <h4>Agent activity</h4><div class="timeline">${acts}</div>
      <h4>Recent commits</h4><div class="timeline">${commits}</div>
    </div>`;
  } catch (e) { body.innerHTML = `<div class="empty">Failed: ${esc(e.message)}</div>`; }
}
$('#proj-refresh').addEventListener('click', loadProjects);

/* ---- Architectures ---- */
const ARCH_STATUS = {
  active:   { icon: '🟢', label: 'active' },
  paused:   { icon: '🟡', label: 'paused' },
  done:     { icon: '🔵', label: 'done' },
  archived: { icon: '⚫', label: 'archived' },
};
const archStatus = s => ARCH_STATUS[String(s || '').toLowerCase()] || { icon: '⚪', label: s || 'unknown' };
const clampPct = n => Math.max(0, Math.min(100, Math.round(n)));
// The server reports progress as a 0–1 fraction; the bar and the label want percent.
const pct = n => clampPct((Number(n) || 0) * 100);
let archById = new Map();
// Catalogs (phases, statuses, priorities) come from the list endpoint so the
// pickers cannot drift from what the server will accept.
let archCatalog = { phases: [], task_priorities: ['P0','P1','P2','P3','P4'],
                    task_priority_default: 'P2', task_statuses: ['todo','doing','blocked','done'] };

/* Summary rows already carry counts, progress and per-phase status — the list
   needs no per-row detail fetch. */
// id -> {icon, tier, name}, so a card can show what shape it was started from.
let archTemplates = new Map();
let runnerAvailable = false;

async function fetchArchList() {
  const [data, tpl] = await Promise.all([
    api('/api/architectures'),
    api('/api/architecture/templates').catch(() => ({ results: [] })),
  ]);
  archTemplates = new Map((tpl.results || []).map(t => [t.id, t]));
  runnerAvailable = !!data.runner_available;
  archCatalog = Object.assign({}, archCatalog, {
    phases: data.phases || [],
    task_priorities: data.task_priorities || archCatalog.task_priorities,
    task_priority_default: data.task_priority_default || archCatalog.task_priority_default,
    task_statuses: data.task_statuses || archCatalog.task_statuses,
  });
  return (data.results || []).map(normalizeArch);
}

function normalizeArch(raw) {
  const counts = raw.counts || {};
  // A summary row carries its own phases, because its template chose them.
  const order = Object.keys(raw.phase_status || {});
  const done = order.filter(p => raw.phase_status[p] === 'done').length;
  return {
    id: raw.id,
    title: raw.title || raw.id,
    summary: raw.summary || '',
    status: String(raw.status || '').toLowerCase(),
    currentPhase: raw.current_phase || '',
    phaseStatus: raw.phase_status || {},
    phaseTotal: order.length,
    phasesDone: done,
    tasksTotal: counts.total || 0,
    tasksDone: counts.done || 0,
    progress: pct(raw.progress),
    repo: raw.repo || '',
    projectId: raw.project_id || '',
    templateId: raw.template_id || '',
    loopCount: raw.loop_count || 0,
    runStatus: raw.run_status || null,
    updated: raw.updated_at || '',
  };
}

const tierBadge = tier => tier
  ? `<span class="tier t${esc(String(tier).toLowerCase())}">${esc(tier)}</span>` : '';

async function loadArchitectures() {
  const el = $('#arch-body');
  el.innerHTML = '<div class="loading"><span class="spinner"></span>Loading architectures…</div>';
  try {
    const list = await fetchArchList();
    archById = new Map(list.map(a => [a.id, a]));
    renderArchList(list);
  } catch (e) {
    el.innerHTML = `<div class="empty">Could not load architectures: ${esc(e.message)}</div>`;
  }
}

function renderArchList(list) {
  stopRunTimers();          // leaving the detail view ends its polling
  const el = $('#arch-body');
  if (!list.length) {
    el.innerHTML = '<div class="empty">No architectures yet — start one with “New Architecture”.</div>';
    return;
  }
  el.innerHTML = archGrid(list);
  bindArchCards();
  scheduleListPoll(list);
}

const archGrid = list => `<div class="arch-grid">${list.map(archCard).join('')}</div>`;

function bindArchCards() {
  $$('#arch-body .arch-card').forEach(c =>
    c.addEventListener('click', () => showArchDetail(c.dataset.arch)));
}

/* The list has to move on its own too: a chain advances phases with nobody in
   the detail view, and a card that only changes on ↻ Refresh is a card that
   lies. Faster while anything is running, slower when the store is quiet. */
let listPollRate = 0;
function scheduleListPoll(list) {
  const rate = (list || []).some(a => a.runStatus === 'running')
    ? RUN_POLL_RUNNING : RUN_POLL_IDLE;
  if (rate === listPollRate && runTimers.length) return;   // cadence unchanged
  stopRunTimers();
  listPollRate = rate;
  runTimers.push(setInterval(refreshArchListLive, rate));
}

async function refreshArchListLive() {
  const el = $('#arch-body');
  if (!el || !el.querySelector('.arch-grid')) return;      // the detail view owns the panel now
  let list;
  try {
    list = await fetchArchList();
  } catch (e) {
    return;                                               // a blip keeps the list we have
  }
  archById = new Map(list.map(a => [a.id, a]));
  const html = archGrid(list);
  // Only repaint on a real change: re-rendering every few seconds would drop
  // hover and focus for nothing.
  if (el.innerHTML !== html) {
    el.innerHTML = html;
    bindArchCards();
  }
  scheduleListPoll(list);
}

function archCard(a) {
  const st = archStatus(a.status);
  const tpl = archTemplates.get(a.templateId);
  const phaseLine = a.phaseTotal
    ? `Phase ${Math.min(a.phasesDone + 1, a.phaseTotal)}/${a.phaseTotal}` + (a.currentPhase ? ' · ' + a.currentPhase : '')
    : (a.currentPhase || 'no phases');
  return `
    <button type="button" class="arch-card" data-arch="${esc(a.id)}">
      <div class="arch-name" title="${esc(a.title)}">${esc(a.title)}</div>
      <div class="arch-status"><span aria-hidden="true">${st.icon}</span><span class="st-text">${esc(st.label)}</span>
        ${tpl ? `<span class="tpl-mini" title="${esc(tpl.name)}">${esc(tpl.icon || '')} ${tierBadge(tpl.tier)}</span>` : ''}</div>
      <div class="arch-phase" title="${esc(phaseLine)}">${esc(phaseLine)}</div>
      <div class="arch-bar" role="progressbar" aria-valuenow="${a.progress}" aria-valuemin="0" aria-valuemax="100"
           aria-label="${esc(a.title)} progress"><i style="--w:${a.progress}%"></i></div>
      <div class="arch-foot">
        <span>${a.tasksDone}/${a.tasksTotal} tasks</span>
        ${a.runStatus === 'running'
          ? '<span class="card-run" title="An agent is working on this now"><span class="dot"></span>running</span>'
          : ''}
        <span class="pct">${a.progress}%</span>
      </div>
    </button>`;
}

const PHASE_MARK = { done: '✓', active: '▸', pending: '·', skipped: '⤳' };

/* ---- Workflow flowchart (inline SVG) ----
   A single top-to-bottom spine: the 16 phases in protocol order, one per row.
   Failure returns run in their own vertical lanes to the left of the spine —
   orthogonal only, so nothing reads as a diagonal shortcut across the chart. */
const FC = { W: 260, H: 34, GAP: 12, ML: 124, MR: 20, MT: 6, MB: 6,
             LANE0: 24, LANE_GAP: 24, R: 6 };
const FC_GLYPH = { done: '✓', active: '●', pending: '○', skipped: '⊘' };

/* Loops are data on the architecture — a template decides them. Lanes are
   assigned deterministically by span, narrowest innermost.

   Nesting, not target position, is what decides whether two returns cross: a
   loop contained inside another must sit inside it, or its short horizontal run
   crosses the outer lane. Sorting by target first does the opposite — it pulls a
   wide early-target loop (approve→spec) inside the narrow ones that end at
   build, and then all three of their runs cross it. Measured on the full-16
   shape: by-target 8 crossings, by-span 1.

   That last one is irreducible: ci→build and approve→spec interleave rather than
   nest, and no left-side-only routing can separate interleaved intervals. */
function laneOrder(loops, order) {
  const at = n => order.indexOf(n);
  return loops
    .filter(l => at(l.from) !== -1 && at(l.to) !== -1)
    .map(l => ({ ...l, label: l.label || 'fail',
                 t: at(l.to), span: Math.abs(at(l.from) - at(l.to)) }))
    .sort((a, b) => a.span - b.span || a.t - b.t || at(a.from) - at(b.from));
}

function fcGeometry(order) {
  const n = order.length;
  return {
    width:  FC.ML + FC.W + FC.MR,
    height: FC.MT + n * FC.H + (n - 1) * FC.GAP + FC.MB,
    y:   r => FC.MT + r * (FC.H + FC.GAP),
    cy:  r => FC.MT + r * (FC.H + FC.GAP) + FC.H / 2,
    lane: i => FC.ML - FC.LANE0 - i * FC.LANE_GAP,
  };
}

/* Out of the source's left edge, into the lane, up it, then into the target's
   left edge. Corners are rounded; every segment is horizontal or vertical. */
function fcLanePath(sy, ty, laneX, edgeX) {
  const r = FC.R, up = ty < sy;
  const v1 = up ? sy - r : sy + r;
  const v2 = up ? ty + r : ty - r;
  return `M${edgeX},${sy} H${laneX + r} Q${laneX},${sy} ${laneX},${v1}`
       + ` V${v2} Q${laneX},${ty} ${laneX + r},${ty} H${edgeX}`;
}

function buildFlowchart(order, phases, loops) {
  const g = fcGeometry(order);
  const rowOf = {};
  order.forEach((name, r) => { rowOf[name] = r; });
  const lanes = laneOrder(loops || [], order);

  // -- forward flow: one short arrow in each gap between consecutive phases --
  const spineX = FC.ML + FC.W / 2;
  const edges = order.slice(0, -1).map((_, r) =>
    `<path class="fc-edge" marker-end="url(#fc-a)"
       d="M${spineX},${g.y(r) + FC.H} V${g.y(r + 1)}"/>`).join('');

  // -- return lanes --
  const loopPaths = [], loopLabels = [];
  lanes.forEach((l, i) => {
    const sy = g.cy(rowOf[l.from]), ty = g.cy(rowOf[l.to]), lx = g.lane(i);
    loopPaths.push(`<path class="fc-loop" marker-end="url(#fc-l)"
      d="${fcLanePath(sy, ty, lx, FC.ML)}"/>`);
    // One chip per loop, at its own lane's vertical midpoint: distinct x by
    // construction, and distinct y because no two lanes share a midpoint.
    const w = l.label.length * 5.6 + 9, my = (sy + ty) / 2;
    loopLabels.push(
      `<rect class="fc-loop-label-bg" x="${lx - w / 2}" y="${my - 6.5}" width="${w}" height="13" rx="4"/>
       <text class="fc-loop-label" x="${lx}" y="${my + 3.5}">${esc(l.label)}</text>`);
  });

  // -- nodes: one compact bar per phase --
  const nodes = order.map((name, r) => {
    const entry = phases[name] || {};
    const status = entry.status || 'pending';
    const counts = entry.counts || {};
    const y = g.y(r);
    return `<g class="fc-node ${esc(status)}" data-phase="${esc(name)}" role="button" tabindex="0"
               aria-label="${esc(name)}: ${esc(status)}, ${counts.done || 0} of ${counts.total || 0} tasks done">
      <rect class="fc-box" x="${FC.ML}" y="${y}" width="${FC.W}" height="${FC.H}" rx="7"/>
      <text class="fc-glyph ${esc(status)}" x="${FC.ML + 12}" y="${y + 22}">${FC_GLYPH[status] || '○'}</text>
      <text class="fc-name" x="${FC.ML + 28}" y="${y + 22}">${esc(name)}</text>
      <text class="fc-badge" x="${FC.ML + FC.W - 12}" y="${y + 22}" text-anchor="end">${counts.done || 0}/${counts.total || 0}</text>
    </g>`;
  }).join('');

  return `<div class="fc-wrap"><div class="fc">
    <svg viewBox="0 0 ${g.width} ${g.height}" role="img"
         aria-label="Workflow: ${order.length} phases top to bottom, with fail loops back to earlier phases">
      <defs>
        <marker id="fc-a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="var(--line)"/>
        </marker>
        <marker id="fc-l" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="var(--bad)"/>
        </marker>
      </defs>
      ${loopPaths.join('')}${edges}${nodes}${loopLabels.join('')}
    </svg></div>
    <div class="fc-legend">
      <span><i></i>forward</span>
      <span><i class="loop"></i>${lanes.length ? 'fail loop — back to an earlier phase'
                                               : 'fail loop — none in this template'}</span>
    </div></div>`;
}

/* ---- Runs ----
   Polling, not a socket: the dashboard is one stdlib HTTP server and a run is a
   subprocess it supervises, so asking every couple of seconds is honest and
   costs less than the machinery a stream would need. Timers are torn down
   whenever the detail view is replaced — a poll against a screen nobody is
   looking at is just load. */
const RUN_POLL_RUNNING = 2500;   // architecture refresh while a run is in flight
const RUN_POLL_IDLE = 5000;      // slower sweep, to catch edits made elsewhere
const LOG_POLL = 1500;
let runTimers = [];
let logView = { run: null, offset: 0, text: '' };

function stopRunTimers() {
  runTimers.forEach(clearInterval);
  runTimers = [];
}

const RUN_LABEL = {
  running: 'running', done: 'done', failed: 'failed',
  killed: 'killed', interrupted: 'interrupted',
};
function runPill(run) {
  const status = (run && run.status) || 'idle';
  const label = RUN_LABEL[status] || 'idle';
  return `<span class="run-pill ${esc(status)}" title="${esc(run && run.error || '')}">
    <span class="dot"></span>${esc(label)}</span>`;
}

async function startRun(id) {
  try {
    const run = await api('/api/architecture/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    toast('Run started — ' + run.id);
    logView = { run: run.id, offset: 0, text: '' };
    await showArchDetail(id);
  } catch (e) {
    toast('Could not start run: ' + e.message, true);
  }
}

async function killRun(id) {
  if (!confirm('Stop this run?\nThe agent process and its children are terminated.')) return;
  try {
    await api('/api/architecture/run/kill', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    toast('Run stopped');
    await showArchDetail(id);
  } catch (e) {
    toast('Could not stop run: ' + e.message, true);
  }
}

/* What to show when the log is still empty. */
function waitingLine(data) {
  if (!data) return 'Loading log…';
  if (data.status !== 'running') {
    return data.size ? '(nothing readable in this run’s log)' : '(this run produced no output)';
  }
  const since = data.started_at ? (Date.now() - Date.parse(data.started_at)) / 1000 : 0;
  const mins = Math.floor(since / 60), secs = Math.floor(since % 60);
  const elapsed = mins ? `${mins}m ${secs}s` : `${secs}s`;
  // size > 0 with nothing rendered means bytes are arriving that we could not
  // read — worth distinguishing from genuine silence.
  return data.size > 0
    ? `running for ${elapsed} — receiving ${data.size} bytes, nothing readable yet`
    : `running for ${elapsed} — waiting for the agent's first message`;
}

/* Incremental tail: ask only for bytes past what we already have. */
async function tailLog(archId) {
  if (!logView.run) return null;
  try {
    const data = await api(`/api/architecture/run/log?id=${encodeURIComponent(archId)}`
      + `&run=${encodeURIComponent(logView.run)}&offset=${logView.offset}`);
    if (data.offset < logView.offset) logView.text = '';   // log was replaced
    logView.text += data.text || '';
    logView.offset = data.offset;
    const box = $('#log-box'), pre = $('#log-pre');
    if (box && pre) {
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      // An agent can think for minutes before its first message lands. Saying so
      // is the difference between "working" and "frozen" — the old placeholder
      // sat there unchanged and read as the latter.
      pre.textContent = logView.text || waitingLine(data);
      if (atBottom && data.text) box.scrollTop = box.scrollHeight;
    }
    return data;
  } catch (e) {
    return null;
  }
}

function runSection(arch, id) {
  const runs = arch.runs || [];
  if (!runs.length) {
    return `<section class="cm-panel"><h3>Agent runs</h3>
      <p class="sub">No runs yet. ▶ Run starts an agent in this repo for the current phase.</p></section>`;
  }
  const current = logView.run || runs[0].id;
  const latest = runs[0];
  // What the shared brain gave this run, and whether it got anything back.
  const memory = latest.memory_write === 'off'
    ? 'memory: off'
    : `memory: ${latest.memory_count || 0} learning${latest.memory_count === 1 ? '' : 's'} injected`
      + (latest.memory_write ? ` · write ${latest.memory_write}` : '');
  const chain = latest.chain_next ? ` · chain: ${latest.chain_next}` : '';
  // A quota refusal exits cleanly and would otherwise read as a normal finish.
  const limited = latest.limit_hit
    ? `<div class="run-warn">⚠️ the agent was refused (${esc(latest.limit_hit)}) — this run did no work.${
        latest.resume_at
          ? ` Picking up again at ${esc(fmtTime(latest.resume_at) || latest.resume_at)}`
            + ` (attempt ${latest.resume_attempt}).`
          : ' Refused too many times to keep retrying — start it again when the limit clears.'}</div>`
    : '';
  return `<section class="cm-panel"><h3>Agent runs</h3>
    <div class="log-head">
      <p class="sub">${esc(latest.command_label || '')} <span class="mem-meta">· ${esc(memory)}${esc(chain)}</span></p>
      <button class="btn ghost" id="log-refresh" style="margin-left:auto">↻ Refresh log</button>
    </div>
    ${limited}
    <div class="run-list">${runs.map(r => `
      <button type="button" class="run-item${r.id === current ? ' sel' : ''}" data-run="${esc(r.id)}"
        title="${esc(r.status)}${r.error ? ' — ' + esc(r.error) : ''}${r.chain_next ? ' · ' + esc(r.chain_next) : ''}">
        ${r.mode ? `<span class="run-mode ${esc(r.mode)}">${esc(r.mode)}</span>` : ''}${
        esc(r.id.replace('run-', ''))} · ${esc(r.status)}${r.chained ? ' ⛓' : ''}</button>`).join('')}</div>
    <div class="log-box" id="log-box"><pre id="log-pre">${esc(logView.text) || 'Loading log…'}</pre></div>
  </section>`;
}

function wireRuns(arch, id) {
  const running = arch.run && arch.run.status === 'running';
  const runBtn = $('#run-start'), killBtn = $('#run-kill');
  if (runBtn) runBtn.addEventListener('click', () => startRun(id));
  if (killBtn) killBtn.addEventListener('click', () => killRun(id));
  $$('#arch-body .run-item').forEach(b => b.addEventListener('click', async () => {
    logView = { run: b.dataset.run, offset: 0, text: '' };
    $('#log-pre').textContent = '';
    await tailLog(id);
    $$('#arch-body .run-item').forEach(x => x.classList.toggle('sel', x === b));
  }));
  const refresh = $('#log-refresh');
  if (refresh) refresh.addEventListener('click', async () => {
    logView.offset = 0; logView.text = '';
    await tailLog(id);
  });

  stopRunTimers();
  // Point logView at a run before tailing. runSection only *displayed* the
  // newest run as selected; without this the first tail after a fresh render
  // saw logView.run === null, returned immediately, and the box sat on
  // "Loading log…" forever — which reads as "▶ Run does nothing".
  const runIds = (arch.runs || []).map(r => r.id);
  if (runIds.length && !runIds.includes(logView.run)) {
    logView = { run: runIds[0], offset: 0, text: '' };
  }
  if (runIds.length) tailLog(id);
  if (running) {
    // The agent reports task progress through the API, so the document itself
    // is what changes — re-read it rather than guessing from the log.
    runTimers.push(setInterval(() => showArchDetail(id, { quiet: true }), RUN_POLL_RUNNING));
    runTimers.push(setInterval(() => tailLog(id), LOG_POLL));
  } else {
    runTimers.push(setInterval(() => showArchDetail(id, { quiet: true }), RUN_POLL_IDLE));
  }
}

/* ---- Per-phase playbook ---- */
function playbookSection(book, phase) {
  const entry = (book || {})[phase] || {};
  const line = (label, names, view) => names && names.length
    ? `<div class="pb-line"><span class="k">${label}</span>${names.map(n =>
        `<button type="button" class="chip" data-jump="${view}" title="Open the ${view} panel">${esc(n)}</button>`).join('')}</div>`
    : `<div class="pb-line"><span class="k">${label}</span><span class="pb-none">none</span></div>`;
  return `<section class="cm-panel"><h3>Playbook · ${esc(phase || '—')}</h3>
    <p class="sub">What to reach for in this phase.${entry.note ? ' ' + esc(entry.note) : ''}</p>
    <div class="pb-groups">
      ${line('Agents', entry.agents, 'agents')}
      ${line('Skills', entry.skills, 'skills')}
      ${line('Rules', entry.rules, 'rules')}
    </div></section>`;
}
const TASK_GROUPS = [
  ['doing', 'In progress'], ['blocked', 'Blocked'], ['todo', 'To do'], ['done', 'Done'],
];

/* Which phase the architecture sits on: the active one, else the first
   unfinished one. Returns order.length when every phase is done — that is the
   "nothing left" state, and it is what disables Mark Complete. */
function currentPhaseIndex(order, phases) {
  const at = order.findIndex(n => (phases[n] || {}).status === 'active');
  if (at !== -1) return at;
  const next = order.findIndex(n => !['done', 'skipped'].includes((phases[n] || {}).status));
  return next === -1 ? order.length : next;
}

/* Before the target → done, the target → active, after → pending.
   Sent as one bulk PUT so the file is never left holding a half-moved workflow
   and progress/current_phase are recomputed once, server-side. */
function phasesForTarget(order, idx) {
  const phases = {};
  order.forEach((name, i) => {
    phases[name] = { status: i < idx ? 'done' : (i === idx ? 'active' : 'pending') };
  });
  return phases;
}

async function setArchPhase(id, order, idx) {
  try {
    await api('/api/architecture/workflow', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, phases: phasesForTarget(order, idx) }),
    });
    toast(idx >= order.length ? 'All phases complete' : 'Now on “' + order[idx] + '”');
    await refreshArchIndex();     // keep the card behind “← All architectures” honest
    await showArchDetail(id);     // re-read: the server owns progress/current_phase
  } catch (e) {
    toast('Could not update phase: ' + e.message, true);
  }
}

/* Refresh the summary index without repainting — the detail view stays on screen. */
async function refreshArchIndex() {
  try {
    const list = await fetchArchList();
    archById = new Map(list.map(a => [a.id, a]));
  } catch (e) {
    /* The detail view is still correct; the list re-reads on ↻ Refresh. */
  }
}

function openPhaseModal(id, name, order, idx) {
  const sel = $('#phase-select');
  sel.innerHTML = order.map((p, i) =>
    `<option value="${i}"${i === idx ? ' selected' : ''}>${i + 1}. ${esc(p)}</option>`).join('');
  $('#phase-modal-meta').textContent = name;
  $('#phase-hint').textContent =
    'Earlier phases are marked done, later ones back to pending.';
  $('#phase-status').textContent = '';
  $('#phase-modal').classList.add('open');
  sel.focus();
  phasePick = { id, order };
}
const closePhaseModal = () => { $('#phase-modal').classList.remove('open'); phasePick = null; };
let phasePick = null;

/* A context file the record points at may no longer be on disk. The row says so
   rather than offering a click that would 404 — the record is still the truth
   about what the work depends on, so the entry is kept, not silently dropped. */
function contextFileRow(c) {
  const missing = !c.exists;
  return `<div class="ctx-row${missing ? ' missing' : ''}" data-ctx="${esc(c.path)}" data-exists="${c.exists}">
    <span class="ctx-mark" aria-hidden="true">${missing ? '⚠️' : '📄'}</span>
    <span class="ctx-path" title="${esc(c.path)}">${esc(c.path.split('/').pop())}</span>
    <span class="ctx-dir">${esc(c.path.replace(/\/[^/]*$/, ''))}</span>
    ${missing ? '<span class="ctx-miss">missing</span>' : ''}
  </div>`;
}

function taskRow(t) {
  const pri = String(t.priority || '').toLowerCase();
  return `<div class="task-row" data-task="${esc(t.id)}" data-phase="${esc(t.phase || '')}">
    <span class="pri ${esc(pri)}">${esc(t.priority || '—')}</span>
    <span class="ttl" title="${esc(t.title || '')}">${esc(t.title || '')}</span>
    <span class="tphase">${esc(t.phase || '')}</span>
    <select class="task-status" aria-label="Status for ${esc(t.title || t.id)}">
      ${archCatalog.task_statuses.map(s =>
        `<option value="${esc(s)}"${s === t.status ? ' selected' : ''}>${esc(s)}</option>`).join('')}
    </select>
    <button class="del-x" data-deltask="${esc(t.id)}" title="Delete task"
            aria-label="Delete ${esc(t.title || t.id)}">✕</button>
  </div>`;
}

async function showArchDetail(id, opts = {}) {
  const el = $('#arch-body');
  // A quiet refresh is a poll: never blank the screen the user is reading, and
  // never fight the scroll position they chose.
  if (!opts.quiet) {
    stopRunTimers();
    el.innerHTML = '<div class="loading"><span class="spinner"></span>Loading architecture…</div>';
  }
  const keepScroll = opts.quiet ? window.scrollY : null;
  let arch;
  try {
    // One call: the detail record carries phases, tasks and the derived counts.
    arch = await api('/api/architecture?id=' + encodeURIComponent(id));
  } catch (e) {
    if (opts.quiet) return;                       // a blip must not wipe the view
    el.innerHTML = `<div class="empty">Could not load ${esc(id)}: ${esc(e.message)}</div>
      <p><button class="btn ghost" id="arch-back">← All architectures</button></p>`;
    $('#arch-back').addEventListener('click', () => renderArchList([...archById.values()]));
    return;
  }

  const phases = arch.phases || {};
  // The document's own phases: a template selects a subset, so the master
  // catalog is the wrong source here.
  const order = Object.keys(phases);
  const counts = arch.counts || {};
  const progress = pct(arch.progress);
  const idx = currentPhaseIndex(order, phases);
  const st = archStatus(arch.status);
  const tasks = Array.isArray(arch.tasks) ? arch.tasks : [];
  const ctx = Array.isArray(arch.context_files) ? arch.context_files : [];
  const title = arch.title || id;
  const tplMeta = archTemplates.get(arch.template_id);
  const running = arch.run && arch.run.status === 'running';
  const runnerReady = runnerAvailable;
  const manualPhase = (arch.manual_phases || []).includes(arch.current_phase);

  const steps = order.length
    ? buildFlowchart(order, phases, arch.loops)
    : '<div class="empty-note">This architecture has no phases.</div>';

  const taskCols = TASK_GROUPS.map(([key, label]) => {
    const rows = tasks.filter(t => t.status === key);
    if (!rows.length) return '';
    return `<div class="task-group"><span class="lbl">${esc(label)} · ${rows.length}</span>
      ${rows.map(taskRow).join('')}</div>`;
  }).join('') || '<div class="empty-note">No tasks yet.</div>';

  const missingCount = ctx.filter(c => !c.exists).length;

  el.innerHTML = `
    <div class="arch-detail-head">
      <button class="btn ghost" id="arch-back">← All architectures</button>
      <h3>${esc(title)}</h3>
      <span class="arch-badge ${esc(st.label)}">${st.icon} ${esc(st.label)}</span>
      ${arch.template_id ? `<span class="tpl-badge" title="Started from the “${esc(tplMeta ? tplMeta.name : arch.template_id)}” template">
        ${esc(tplMeta && tplMeta.icon || '📐')} ${esc(arch.template_id)}${tplMeta ? tierBadge(tplMeta.tier) : ''}</span>` : ''}
      ${manualPhase ? `<span class="manual-badge" title="Approval, release and reflection are decisions a person makes — approve or send it back here, with the buttons on this row">
        ⚠️ waiting for you</span>` : ''}
      ${runPill(arch.run)}
      <div class="arch-actions">
        ${running
          ? `<button class="btn ghost" id="run-kill" title="Terminate the agent process group">✕ Kill</button>`
          : `<button class="btn ghost" id="run-start" ${runnerReady ? '' : 'disabled'}
               title="${runnerReady ? (manualPhase
                      ? 'Send an agent to check the open tasks and write a verdict into each — it decides nothing'
                      : 'Start an agent for the current phase')
                                    : 'claude is not on PATH — nothing to launch'}">${
                 manualPhase ? '▶ Prepare approval' : '▶ Run'}</button>`}
        <button class="btn ghost" id="chain-toggle"
          title="${arch.auto_chain
            ? 'Each finished run starts the next, up to a cap; stops at manual phases'
            : 'Runs are started one at a time, by you'}">
          Chain: ${arch.auto_chain ? 'ON' : 'OFF'}</button>
        <button class="btn ghost" id="auto-toggle"
          title="${arch.auto_advance_disabled
            ? 'Phases only move when you say so'
            : 'A finished phase moves on by itself, except approve / release / learn'}">
          Auto-advance: ${arch.auto_advance_disabled ? 'OFF' : 'ON'}</button>
        ${manualPhase ? `
        <button class="btn ghost" id="phase-prev" ${idx <= 0 ? 'disabled' : ''}
          title="Reject: send the work back to the phase before this gate">← Send back</button>
        <button class="btn primary" id="phase-next" ${idx >= order.length ? 'disabled' : ''}
          title="Approve this gate — you will be asked to confirm">✓ Approve &amp; continue</button>` : ''}
        <button class="btn primary" id="phase-update" ${order.length ? '' : 'disabled'}>Update Phase</button>
        <button class="btn ghost danger" id="arch-delete"
          title="Delete this architecture and its tasks — this cannot be undone">🗑 Delete</button>
      </div>
    </div>
    ${arch.summary ? `<p class="muted">${esc(arch.summary)}</p>` : ''}
    <p class="muted arch-meta">
      <span title="Repository this architecture is bound to">📁 ${esc(arch.repo || '—')}</span>
      <span title="mem0 scope">🧠 ${esc(arch.project_id || '—')}</span>
      ${arch.current_feature ? `<span title="Current feature">▸ ${esc(arch.current_feature)}</span>` : ''}
    </p>
    <div class="cm-stats">
      <div class="cm-stat"><b>${idx >= order.length ? order.length : idx + 1}</b>/ ${order.length || '—'} phases</div>
      <div class="cm-stat"><b>${counts.done || 0}</b>/ ${counts.total || 0} tasks done</div>
      <div class="cm-stat" title="Phases finished, plus how far into the current one — not tasks alone"><b>${progress}%</b> complete</div>
    </div>
    <div class="arch-bar" role="progressbar" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"
         aria-label="${esc(title)} progress" style="margin-bottom:1.2rem"><i style="--w:${progress}%"></i></div>
    <div class="arch-detail-grid">
      <div class="arch-left">
        <section class="cm-panel"><h3>Workflow</h3>
          <p class="sub">${esc(arch.current_phase || (order.length ? 'all phases done' : 'no phases'))}</p>${steps}</section>
      </div>
      <div class="arch-right">
      <div id="pb-section">${playbookSection(arch.playbook, arch.current_phase)}</div>
      <section class="cm-panel"><h3>Tasks</h3>
        <p class="sub">${counts.done || 0} of ${counts.total || 0} done</p>
        <div class="task-add">
          <input type="text" id="task-title" placeholder="New task…" aria-label="Task title">
          <select id="task-prio" aria-label="Priority">
            ${archCatalog.task_priorities.map(p =>
              `<option value="${esc(p)}"${p === archCatalog.task_priority_default ? ' selected' : ''}>${esc(p)}</option>`).join('')}
          </select>
          <select id="task-phase" aria-label="Phase">
            ${order.map(p =>
              `<option value="${esc(p)}"${p === arch.current_phase ? ' selected' : ''}>${esc(p)}</option>`).join('')}
          </select>
          <button class="btn primary" id="task-add">Add</button>
        </div>
        ${taskCols}</section>
      ${autoAdvanceSection(arch)}
      ${runSection(arch, id)}
      ${ctx.length ? `<section class="cm-panel"><h3>Context files</h3>
        <p class="sub">${ctx.length} referenced${missingCount ? ` · ⚠️ ${missingCount} missing` : ''}</p>
        ${ctx.map(contextFileRow).join('')}</section>` : ''}
      </div>
    </div>`;
  if (keepScroll !== null) window.scrollTo(0, keepScroll);

  $('#arch-back').addEventListener('click', () => renderArchList([...archById.values()]));
  // Both gate controls exist only while a gate is holding, so both are wired
  // only then — $() returns null otherwise and addEventListener would throw,
  // taking the rest of this function's wiring with it.
  if (manualPhase) {
    $('#phase-next').addEventListener('click', () => openApproveModal(
      id, arch.current_phase, order[idx + 1], order, idx,
      tasks.filter(t => t.phase === arch.current_phase && t.status !== 'done').length));
    $('#phase-prev').addEventListener('click', () => setArchPhase(id, order, idx - 1));
  }
  $('#phase-update').addEventListener('click', () =>
    openPhaseModal(id, title, order, Math.min(idx, order.length - 1)));
  $('#auto-toggle').addEventListener('click', () =>
    setAutoAdvance(id, !!arch.auto_advance_disabled));
  $('#chain-toggle').addEventListener('click', () => setAutoChain(id, !arch.auto_chain));
  $('#arch-delete').addEventListener('click', () => deleteArchitecture(id, title));
  $('#task-add').addEventListener('click', () => addArchTask(id));
  $('#task-title').addEventListener('keydown', e => { if (e.key === 'Enter') addArchTask(id); });

  const wireChips = () => $$('#pb-section .chip[data-jump]').forEach(chip =>
    chip.addEventListener('click', () => show(chip.dataset.jump)));
  wireChips();

  // Selecting a phase is one gesture with three effects: the playbook follows it,
  // its tasks light up, and the view moves to them. The spine stays put (it is
  // sticky), so the selection always has its reference next to it.
  const focusPhase = name => {
    $$('#arch-body .fc-node').forEach(n => n.classList.toggle('selected', n.dataset.phase === name));
    $('#pb-section').innerHTML = playbookSection(arch.playbook, name);
    wireChips();
    const rows = $$('#arch-body .task-row');
    rows.forEach(r => r.classList.toggle('hl', r.dataset.phase === name));
    const first = rows.find(r => r.dataset.phase === name);
    (first || $('#arch-body .task-add')).scrollIntoView({ block: 'center', behavior: 'smooth' });
    if (!first) toast('No tasks in “' + name + '” yet');
  };
  $$('#arch-body .fc-node').forEach(node => {
    node.addEventListener('click', () => focusPhase(node.dataset.phase));
    node.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); focusPhase(node.dataset.phase); }
    });
  });

  $$('#arch-body .task-status').forEach(sel => sel.addEventListener('change', () =>
    patchArchTask(id, sel.closest('.task-row').dataset.task, { status: sel.value })));
  $$('#arch-body [data-deltask]').forEach(btn => btn.addEventListener('click', () =>
    deleteArchTask(id, btn.dataset.deltask)));
  // Not openFileModal: that renders any failure as a "Failed to open" editor
  // full of guard internals. A context file lives in a repo, and /api/content
  // deliberately serves only the managed AI-context roots — so a file that
  // plainly exists can still be un-openable, and the row should say that in one
  // line rather than opening a modal to explain it.
  wireRuns(arch, id);
  $$('#arch-body .ctx-row').forEach(row => row.addEventListener('click', async () => {
    const path = row.dataset.ctx, name = path.split('/').pop();
    if (row.dataset.exists !== 'true') return toast('File not found: ' + path, true);
    try {
      const data = await api('/api/content?path=' + encodeURIComponent(path));
      openModal({ title: data.name, meta: data.path, content: data.content,
                  editable: data.editable,
                  state: data.editable ? { type: 'file', path: data.path } : null });
    } catch (e) {
      toast('Cannot open ' + name + ': ' + e.message, true);
    }
  }));
}

/* The server assigns the id, timestamps and "todo" status; the form only carries
   what a human chooses. Re-reads the detail afterwards so the new row lands in the
   right status group with the counts the server computed. */
async function addArchTask(id) {
  const title = $('#task-title').value.trim();
  if (!title) return toast('Task needs a title', true);
  const btn = $('#task-add');
  btn.disabled = true;
  try {
    await api('/api/architecture/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        arch: id,
        title,
        priority: $('#task-prio').value,
        phase: $('#task-phase').value,
      }),
    });
    toast('Added “' + title + '”');
    await refreshArchIndex();
    await showArchDetail(id);
  } catch (e) {
    btn.disabled = false;
    toast('Could not add task: ' + e.message, true);
  }
}

/* Status and priority edits re-read the record rather than patching the DOM:
   the server owns which group a task belongs to and what the counts are. */
async function patchArchTask(id, taskId, patch) {
  try {
    const doc = await api('/api/architecture/task', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ arch: id, id: taskId }, patch)),
    });
    // The write may have finished the phase; the server says so in the reply
    // rather than making the panel diff two documents to notice.
    if (doc.auto_advanced) {
      toast(`Auto-advanced: ${doc.auto_advanced.from} → ${doc.auto_advanced.to}`);
    }
    await refreshArchIndex();
    await showArchDetail(id);
  } catch (e) {
    toast('Could not update task: ' + e.message, true);
    await showArchDetail(id);   // put the select back where the record says it is
  }
}

async function setAutoAdvance(id, enabled) {
  try {
    await api('/api/architecture/' + (enabled ? 'resume-auto' : 'pause-auto'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    toast(enabled ? 'Auto-advance resumed' : 'Auto-advance paused');
    await showArchDetail(id);
  } catch (e) {
    toast('Could not change auto-advance: ' + e.message, true);
  }
}

async function setAutoChain(id, on) {
  if (on && !confirm('Let this architecture start its own runs?\n\nEach finished run '
      + 'launches the next, up to 6, stopping at a manual phase or a failure. '
      + 'Agents will edit this repo without being asked again.')) return;
  try {
    await api('/api/architecture/chain', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, on }),
    });
    toast(on ? 'Auto-chain on' : 'Auto-chain off');
    await showArchDetail(id);
  } catch (e) {
    toast('Could not change auto-chain: ' + e.message, true);
  }
}

function autoAdvanceSection(arch) {
  const log = (arch.auto_advance_log || []).slice(-5).reverse();
  const manual = (arch.manual_phases || []).includes(arch.current_phase);
  const blockers = arch.auto_advance_blockers || [];
  const why = arch.auto_advance_disabled
    ? 'Paused — phases only move when you say so.'
    : (blockers.length ? 'Holding: ' + blockers.join('; ')
                       : 'Ready — the next report that finishes this phase moves it on.');
  return `<section class="cm-panel"><h3>Auto-advance</h3>
    <p class="sub">${manual ? '⚠️ ' : ''}${esc(why)}</p>
    ${log.length ? `<div class="aa-log">${log.map(e => {
      const back = e.kind === 'rollback';
      return `<div class="aa-row"><span class="aa-kind ${back ? 'back' : ''}"
          title="${back ? 'moved back' : 'advanced'}">${back ? '↩' : '→'}</span>
        <span class="aa-move">${esc(e.from)} ${back ? '←' : '→'} ${esc(e.to)}</span>
        ${back ? '<span class="aa-n">moved back</span>'
               : `<span class="aa-n">${e.tasks_completed} task${e.tasks_completed === 1 ? '' : 's'}</span>`}
        <span class="aa-when">${esc(fmtTime(e.triggered_at) || e.triggered_at || '')}</span></div>`;
      }).join('')}</div>`
      : '<div class="empty-note">No automatic advances yet.</div>'}
  </section>`;
}

/* Deleting the architecture unlinks the file the detail view is drawn from, so
   the only honest place to land afterwards is the list. */
async function deleteArchitecture(id, title) {
  if (!confirm(`Delete "${title}"?\nThe architecture and all of its tasks are removed from disk. This cannot be undone.`)) return;
  try {
    await api(`/api/architecture?id=${encodeURIComponent(id)}`, { method: 'DELETE' });
    toast('Architecture deleted');
    // Dropped locally as well: refreshArchIndex swallows its own failures, and a
    // card that outlives its document only 404s when someone clicks it.
    archById.delete(id);
    await refreshArchIndex();
    renderArchList([...archById.values()]);
  } catch (e) {
    toast('Could not delete architecture: ' + e.message, true);
  }
}

async function deleteArchTask(id, taskId) {
  if (!confirm('Delete this task?')) return;
  try {
    const doc = await api(`/api/architecture/task?arch=${encodeURIComponent(id)}&id=${encodeURIComponent(taskId)}`,
              { method: 'DELETE' });
    if (doc.auto_advanced) {
      toast(`Auto-advanced: ${doc.auto_advanced.from} → ${doc.auto_advanced.to}`);
    }
    toast('Task deleted');
    await refreshArchIndex();
    await showArchDetail(id);
  } catch (e) {
    toast('Could not delete task: ' + e.message, true);
  }
}

$('#phase-close').addEventListener('click', closePhaseModal);
$('#phase-cancel').addEventListener('click', closePhaseModal);
$('#phase-modal').addEventListener('click', e => { if (e.target.id === 'phase-modal') closePhaseModal(); });
$('#phase-save').addEventListener('click', async () => {
  if (!phasePick) return;
  const { id, order } = phasePick;
  const idx = Number($('#phase-select').value);
  closePhaseModal();
  await setArchPhase(id, order, idx);
});

$('#arch-refresh').addEventListener('click', loadArchitectures);
$('#arch-new').addEventListener('click', openNewArchModal);

/* ---- Harness Studio ---- */
let currentHarnessRunId = null;

async function loadHarness() {
  const body = $('#harness-body');
  if (!body) return;
  body.innerHTML = `
    <div class="harness-grid">
      <!-- Mission Control Card -->
      <div class="harness-card">
        <h3><span>🎯 Mission Control</span> <span class="badge" style="color:var(--ok);border:1px solid currentColor;">🛡️ Guardrails Active</span></h3>
        <div class="harness-form-group">
          <label for="harness-task">Task Description or Prompt</label>
          <textarea id="harness-task" placeholder="e.g. Inspect PostgreSQL pool connections and run test suite"></textarea>
        </div>
        <div class="add-memory-row">
          <div class="harness-form-group">
            <label for="harness-scope">Project Scope</label>
            <input type="text" id="harness-scope" value="mem0-local" placeholder="e.g. mem0-local, personal">
          </div>
          <div class="empty-note">Task execution and verification are unavailable until a trusted executor is configured.</div>
        </div>
        <div class="harness-actions">
          <button class="btn primary" id="harness-run-btn">▶ Prepare Harness Context</button>
          <button class="btn ghost" id="harness-inspect-btn">🔍 Inspect Pre-flight Context</button>
        </div>
        <div id="harness-alert-box" style="display:none;"></div>
      </div>

      <!-- Architectural Hubs Card -->
      <div class="harness-card">
        <h3><span>🕸️ Architectural Hubs (Degree Centrality)</span></h3>
        <p style="font-size:0.82rem;color:var(--text-dim);margin:0;">Core connected technologies, files, and architectural concepts across knowledge graph.</p>
        <div id="harness-hubs-container" class="hubs-grid"><div class="loading"><span class="spinner"></span>Loading hubs…</div></div>
      </div>
    </div>

    <!-- Pre-flight Context X-Ray -->
    <div class="harness-card" id="harness-context-card" style="display:none;margin-bottom:1.25rem;">
      <h3><span>🔍 Pre-flight Context Assembly (Agent Brain Injection)</span> <button class="btn ghost" id="harness-context-close" style="font-size:0.75rem;padding:0.15rem 0.5rem;">✕ Close</button></h3>
      <div id="harness-context-content" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));gap:1rem;"></div>
    </div>

    <!-- Live Trajectory Timeline & Terminal -->
    <div class="harness-grid">
      <!-- Steps Trace -->
      <div class="harness-card">
        <h3><span>⚡ Trajectory Timeline</span> <span id="harness-status-pill" class="arch-badge">Ready</span></h3>
        <div id="harness-steps" class="harness-stepper">
          <div class="empty-note">No active run. Enter a task above and click 'Run under Harness'.</div>
        </div>
      </div>

      <!-- Terminal Output -->
      <div class="harness-card">
        <h3><span>💻 Live Terminal / Verification</span></h3>
        <div id="harness-terminal" class="harness-terminal">// Harness console waiting for execution…</div>
      </div>
    </div>

    <!-- Recent Runs -->
    <div class="harness-card" style="margin-top:1.25rem;">
      <h3><span>📜 Recent Trajectories (~/.mem0/trajectories)</span></h3>
      <div id="harness-runs-table"><div class="loading"><span class="spinner"></span>Loading runs…</div></div>
    </div>
  `;

  // Bind actions
  $('#harness-run-btn').addEventListener('click', executeHarnessRun);
  $('#harness-inspect-btn').addEventListener('click', inspectHarnessContext);
  $('#harness-context-close').addEventListener('click', () => {
    $('#harness-context-card').style.display = 'none';
  });
  if ($('#harness-refresh')) {
    $('#harness-refresh').addEventListener('click', () => {
      loadHarnessHubs();
      loadHarnessRuns();
    });
  }

  // Load hubs and recent runs
  loadHarnessHubs();
  loadHarnessRuns();
}

async function loadHarnessHubs() {
  const container = $('#harness-hubs-container');
  if (!container) return;
  try {
    const data = await api('/api/graph/hubs?top_k=12');
    const hubs = data.hubs || [];
    if (!hubs.length) {
      container.innerHTML = '<span class="empty-note">No graph entities indexed yet.</span>';
      return;
    }
    container.innerHTML = hubs.map(h => `
      <span class="hub-chip" title="${esc(h.label || h.id)}">
        <span class="hub-deg">${h.total_degree}★</span>
        <span>${esc(h.id)}</span>
      </span>
    `).join('');
  } catch (e) {
    container.innerHTML = `<span class="empty-note">Could not load hubs: ${esc(e.message)}</span>`;
  }
}

async function inspectHarnessContext() {
  const task = ($('#harness-task').value || '').trim();
  const scope = ($('#harness-scope').value || 'personal').trim();
  const card = $('#harness-context-card');
  const content = $('#harness-context-content');
  if (!task) {
    toast('Please enter a task description first', true);
    return;
  }
  card.style.display = 'flex';
  content.innerHTML = '<div class="loading"><span class="spinner"></span>Assembling pre-flight context…</div>';

  try {
    const res = await api(`/api/harness/context?task=${encodeURIComponent(task)}&scope=${encodeURIComponent(scope)}`);
    const ctx = res.context || {};
    content.innerHTML = `
      <div style="background:var(--bg-elev-2);border:1px solid var(--line);border-radius:6px;padding:0.75rem;">
        <div style="font-weight:600;color:var(--accent);margin-bottom:0.4rem;">🧠 Scoped Memories</div>
        <div style="font-size:0.8rem;color:var(--text-dim);max-height:160px;overflow-y:auto;white-space:pre-wrap;">${esc(ctx.memories || '(no scoped memories matched)')}</div>
      </div>
      <div style="background:var(--bg-elev-2);border:1px solid var(--line);border-radius:6px;padding:0.75rem;">
        <div style="font-weight:600;color:var(--ok);margin-bottom:0.4rem;">📜 Binding Rules</div>
        <div style="font-size:0.8rem;color:var(--text-dim);max-height:160px;overflow-y:auto;white-space:pre-wrap;">${esc(ctx.rules || '(no architectural rules bound)')}</div>
      </div>
      <div style="background:var(--bg-elev-2);border:1px solid var(--line);border-radius:6px;padding:0.75rem;">
        <div style="font-weight:600;color:var(--warn);margin-bottom:0.4rem;">✦ Matched Skills</div>
        <div style="font-size:0.8rem;color:var(--text-dim);max-height:160px;overflow-y:auto;white-space:pre-wrap;">${esc(ctx.skills || '(no special skills activated)')}</div>
      </div>
      <div style="background:var(--bg-elev-2);border:1px solid var(--line);border-radius:6px;padding:0.75rem;">
        <div style="font-weight:600;color:var(--brain);margin-bottom:0.4rem;">🕸️ Graph Entities (${(ctx.graph || []).length})</div>
        <div style="font-size:0.8rem;color:var(--text-dim);max-height:160px;overflow-y:auto;">
          ${(ctx.graph || []).map(g => `<div>• [${esc(g.relation || 'rel')}] <strong>${esc(g.node_id || g.id)}</strong> (${esc(g.kind || 'node')})</div>`).join('') || '(no graph entities)'}
        </div>
      </div>
    `;
  } catch (e) {
    content.innerHTML = `<div class="empty-note">Failed to assemble context: ${esc(e.message)}</div>`;
  }
}

async function executeHarnessRun() {
  const task = ($('#harness-task').value || '').trim();
  const scope = ($('#harness-scope').value || 'personal').trim();
  const alertBox = $('#harness-alert-box');
  const terminal = $('#harness-terminal');
  const stepsContainer = $('#harness-steps');
  const statusPill = $('#harness-status-pill');
  const btn = $('#harness-run-btn');

  if (!task) {
    toast('Please enter a task description', true);
    return;
  }

  btn.disabled = true;
  btn.textContent = '⏳ Preparing…';
  alertBox.style.display = 'none';
  statusPill.textContent = 'Running';
  statusPill.className = 'arch-badge active';
  terminal.textContent = `[HARNESS START] Task: "${task}"\nScope: ${scope}\nAttempting pre-flight context assembly…\n`;

  try {
    const res = await api('/api/harness/run', {
      method: 'POST',
      body: JSON.stringify({ task, scope }),
    });

    currentHarnessRunId = res.run_id;

    if (res.status === 'blocked') {
      statusPill.textContent = 'Blocked by Safety';
      statusPill.className = 'arch-badge completed';
      alertBox.className = 'harness-alert';
      alertBox.style.display = 'flex';
      alertBox.innerHTML = `
        <strong>⚠️ SAFETY GUARDRAIL VIOLATION INTERCEPTED</strong>
        <span>${esc(res.safety_violation || 'Dangerous command blocked')}</span>
      `;
      terminal.textContent += `\n[BLOCKED] Harness did not proceed.\n`;
    } else {
      statusPill.textContent = 'Not executed';
      statusPill.className = 'arch-badge paused';
      alertBox.className = 'harness-alert';
      alertBox.style.display = 'flex';
      alertBox.innerHTML = `
        <strong>⚡ CONTEXT ASSEMBLY ATTEMPTED · TASK NOT EXECUTED</strong>
        <span>Run ID: ${esc(res.run_id)} | Status: ${esc(res.status)} | Verification: NOT RUN</span>
      `;
      terminal.textContent += `\n[NOT EXECUTED] Run ID: ${res.run_id}\n${res.message || 'No trusted task executor is configured.'}\n`;
    }

    if (res.run_id) {
      await loadHarnessTrajectoryDetail(res.run_id);
    }
    await loadHarnessRuns();
  } catch (e) {
    statusPill.textContent = 'Error';
    statusPill.className = 'arch-badge completed';
    terminal.textContent += `\n[ERROR] ${e.message}\n`;
    toast('Harness execution failed: ' + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Prepare Harness Context';
  }
}

async function loadHarnessTrajectoryDetail(runId) {
  const stepsContainer = $('#harness-steps');
  if (!stepsContainer) return;
  try {
    const data = await api(`/api/harness/trajectory?id=${encodeURIComponent(runId)}`);
    const steps = data.steps || [];
    if (!steps.length) {
      stepsContainer.innerHTML = '<div class="empty-note">No recorded trajectory steps.</div>';
      return;
    }
    stepsContainer.innerHTML = steps.map(s => {
      const act = s.action || 'step';
      let summary = '';
      if (s.task) summary = `Task: ${s.task}`;
      else if (s.command) summary = `Command: ${s.command}`;
      else if (s.violation) summary = `Blocked: ${s.violation}`;
      else if (s.has_memories !== undefined) summary = `Context: memories=${s.has_memories}, rules=${s.has_rules}, skills=${s.has_skills}`;
      else if (s.output_preview) summary = `Output: ${s.output_preview}`;
      else summary = JSON.stringify(s);

      return `
        <div class="harness-step-item">
          <span class="badge">Step ${s.step || 1}</span>
          <strong style="font-size:0.78rem;text-transform:uppercase;">${esc(act)}</strong>
          <span style="color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(summary)}</span>
        </div>
      `;
    }).join('');
  } catch (e) {
    stepsContainer.innerHTML = `<div class="empty-note">Could not load steps: ${esc(e.message)}</div>`;
  }
}

async function loadHarnessRuns() {
  const table = $('#harness-runs-table');
  if (!table) return;
  try {
    const data = await api('/api/harness/trajectories');
    const runs = data.trajectories || [];
    if (!runs.length) {
      table.innerHTML = '<div class="empty-note">No recent trajectories found in ~/.mem0/trajectories/</div>';
      return;
    }
    table.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:0.35rem;">
        ${runs.map(r => `
          <div class="flight-row" style="cursor:pointer;" onclick="loadHarnessTrajectoryDetail('${esc(r.run_id)}')">
            <span class="arch-badge ${r.status === 'completed' ? 'active' : (r.status === 'blocked' ? 'completed' : 'paused')}">${esc(r.status)}</span>
            <span style="font-weight:600;font-size:0.86rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(r.task)}</span>
            <span style="font-family:var(--mono);font-size:0.75rem;color:var(--accent);">${esc(r.scope)}</span>
            <span style="font-family:var(--mono);font-size:0.75rem;color:var(--text-faint);">${r.step_count} steps</span>
            <span style="margin-left:auto;font-family:var(--mono);font-size:0.75rem;color:var(--text-faint);">${esc(r.created_at ? r.created_at.slice(0, 16).replace('T', ' ') : '')}</span>
          </div>
        `).join('')}
      </div>
    `;
  } catch (e) {
    table.innerHTML = `<div class="empty-note">Could not load runs: ${esc(e.message)}</div>`;
  }
}

/* ---- Assistant / Ask Brain ---- */
let chatHistory = [];
let assistantInitialized = false;

async function loadAssistant() {
  const scopeSelect = $('#chat-scope');
  if (scopeSelect && scopeSelect.options.length <= 1) {
    try {
      const data = await api('/api/memories');
      if (data && data.scopes) {
        const current = scopeSelect.value || 'personal';
        const scopes = Object.keys(data.scopes);
        if (!scopes.includes('personal')) scopes.unshift('personal');
        scopeSelect.innerHTML = scopes.map(s =>
          `<option value="${esc(s)}"${s === current ? ' selected' : ''}>${esc(s)} (${data.scopes[s] || 0} mems)</option>`
        ).join('');
      }
    } catch (_) {}
  }

  if (assistantInitialized) return;
  assistantInitialized = true;

  const input = $('#chat-input');
  const sendBtn = $('#chat-send');
  const clearBtn = $('#chat-clear');
  const msgBox = $('#chat-messages');

  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      chatHistory = [];
      msgBox.innerHTML = `
        <div class="chat-msg ai">
          <div class="chat-msg-header"><span class="chat-avatar">🧠</span><strong>Shared Brain</strong><span class="chat-time">Ready</span></div>
          <div class="chat-msg-bubble">Conversation cleared. What would you like to explore or check?</div>
        </div>
      `;
    });
  }

  async function handleSend() {
    const text = (input.value || '').trim();
    if (!text) return;
    const scope = (scopeSelect ? scopeSelect.value : 'personal') || 'personal';

    // Append User Message
    const userMsgEl = document.createElement('div');
    userMsgEl.className = 'chat-msg user';
    userMsgEl.innerHTML = `
      <div class="chat-msg-header"><strong>You</strong><span class="chat-avatar">👤</span></div>
      <div class="chat-msg-bubble">${esc(text)}</div>
    `;
    msgBox.appendChild(userMsgEl);

    input.value = '';
    input.style.height = 'auto';

    // Append AI Thinking Message
    const aiMsgEl = document.createElement('div');
    aiMsgEl.className = 'chat-msg ai';
    aiMsgEl.innerHTML = `
      <div class="chat-msg-header"><span class="chat-avatar">🧠</span><strong>Shared Brain</strong><span class="chat-time">Recalling &amp; Thinking…</span></div>
      <div class="chat-msg-bubble"><span class="spinner" style="display:inline-block;vertical-align:middle;margin-right:6px;"></span> Consulting memories &amp; rules…</div>
    `;
    msgBox.appendChild(aiMsgEl);
    msgBox.scrollTop = msgBox.scrollHeight;

    sendBtn.disabled = true;

    try {
      const res = await api('/api/assistant/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text, scope, history: chatHistory })
      });

      const answer = res.answer || 'No response received.';
      const memories = res.memories_used || [];
      const rules = res.rules_applied || [];

      let sourcesHtml = '';
      if (memories.length > 0 || rules.length > 0) {
        sourcesHtml = `
          <details class="chat-grounding">
            <summary>Grounded in ${memories.length} memory/memories &amp; ${rules.length} rule(s)</summary>
            <ul>
              ${memories.map(m => `
                <li>
                  <span class="score">${(m.score || 0).toFixed(2)}</span>
                  <span style="color:var(--accent);">[${esc(m.scope)}]</span>
                  ${esc(m.memory)}
                  ${(m.metadata && m.metadata.graph_expanded) ? '<b style="color:var(--brain);"> [Graph]</b>' : ''}
                </li>
              `).join('')}
              ${rules.map(r => `
                <li>
                  <span style="color:var(--warn);">[RULE: ${esc(r.id || 'rule')}]</span>
                  ${esc(r.rule || '')}
                </li>
              `).join('')}
            </ul>
          </details>
        `;
      }

      aiMsgEl.innerHTML = `
        <div class="chat-msg-header"><span class="chat-avatar">🧠</span><strong>Shared Brain</strong><span class="chat-time">Just now</span></div>
        <div class="chat-msg-bubble">${esc(answer)}${sourcesHtml}</div>
      `;

      chatHistory.push({ role: 'user', content: text });
      chatHistory.push({ role: 'assistant', content: answer });
      if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);
    } catch (err) {
      aiMsgEl.innerHTML = `
        <div class="chat-msg-header"><span class="chat-avatar">🧠</span><strong>Shared Brain</strong><span class="chat-time" style="color:var(--bad);">Error</span></div>
        <div class="chat-msg-bubble" style="border-color:var(--bad);color:var(--bad);">Failed to get answer: ${esc(err.message)}</div>
      `;
    } finally {
      sendBtn.disabled = false;
      msgBox.scrollTop = msgBox.scrollHeight;
    }
  }

  if (sendBtn) sendBtn.addEventListener('click', handleSend);
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });
  }
}

/* ---- Boot ---- */
const initial = (location.hash || '#overview').slice(1);
show(['overview','assistant','projects','codemap','architectures','harness','memory','skills','agents','mcp','rules','hooks'].includes(initial) ? initial : 'overview');
</script>
</body>
</html>
"""
