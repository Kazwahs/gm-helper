// Small helpers shared by the tool pages. Kept dependency-free on purpose
// so the app doesn't need a Node build step.

async function postForm(url, data) {
  const body = new URLSearchParams();
  Object.entries(data).forEach(([k, v]) => body.append(k, v));
  const res = await fetch(url, { method: "POST", body });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || "Request failed");
  return json;
}

// Sends one line to the session log of the game's most recently active campaign
// (the session being played). Resolves with a short confirmation like
// "Logged to Shadows over Chi-Town, session 3".
async function logToSession(text, kind = "note") {
  const res = await postForm("/api/log", { game: document.body.dataset.game || "", text, kind });
  return "Logged to " + res.campaign_title + (res.session_number ? ", session " + res.session_number : "");
}


function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  });
  (Array.isArray(children) ? children : [children]).forEach((c) => {
    if (c === null || c === undefined) return;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  });
  return node;
}


// Minimal, safe Markdown -> DOM. Supports **bold**, *italic*, `code`,
// "- " / "* " bullet lists, "1. " numbered lists, "## " headings and blank-line
// paragraphs. Everything is built with createElement/textContent, so card or
// AI text can never inject markup.
function renderInline(text) {
  const frag = document.createDocumentFragment();
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
    const tok = m[0];
    if (tok.startsWith("**")) frag.appendChild(el("strong", {}, tok.slice(2, -2)));
    else if (tok.startsWith("`")) frag.appendChild(el("code", {}, tok.slice(1, -1)));
    else frag.appendChild(el("em", {}, tok.slice(1, -1)));
    last = m.index + tok.length;
  }
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
  return frag;
}

function renderMarkdown(text) {
  const root = document.createDocumentFragment();
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  let para = [], list = null, listType = null;
  const flushPara = () => {
    if (!para.length) return;
    const p = el("p");
    para.forEach((line, i) => {
      if (i) p.appendChild(el("br"));
      p.appendChild(renderInline(line));
    });
    root.appendChild(p);
    para = [];
  };
  const flushList = () => { if (list) { root.appendChild(list); list = null; listType = null; } };
  lines.forEach((raw) => {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flushPara(); flushList(); return; }
    if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      flushPara();
      if (listType !== "ul") { flushList(); list = el("ul"); listType = "ul"; }
      list.appendChild(el("li", {}, [renderInline(m[1])]));
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (listType !== "ol") { flushList(); list = el("ol"); listType = "ol"; }
      list.appendChild(el("li", {}, [renderInline(m[1])]));
    } else if ((m = line.match(/^#{1,4}\s+(.*)$/))) {
      flushPara(); flushList();
      root.appendChild(el("h4", {}, [renderInline(m[1])]));
    } else {
      flushList();
      para.push(line);
    }
  });
  flushPara(); flushList();
  return root;
}


// Navigation: phone hamburger, dropdowns (Escape / click-away close them), and
// remembering the last game so Library and Campaigns stay in the menu on pages
// that have no game of their own (Settings, Bookmarks...).
document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("nav-toggle");
  const nav = document.getElementById("site-nav");
  const setNavOpen = (open) => {
    if (!nav || !toggle) return;
    nav.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  };
  if (toggle && nav) {
    toggle.addEventListener("click", () => setNavOpen(!nav.classList.contains("open")));
  }

  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.nav-menu[open]").forEach((d) => {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
    // tapping the page (not the header) folds the phone menu back up
    if (nav && nav.classList.contains("open") && !e.target.closest("header.topbar")) setNavOpen(false);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const openMenu = document.querySelector("details.nav-menu[open]");
    if (openMenu) {
      openMenu.removeAttribute("open");
      const summary = openMenu.querySelector("summary");
      if (summary) summary.focus();
    } else if (nav && nav.classList.contains("open")) {
      setNavOpen(false);
      if (toggle) toggle.focus();
    }
  });

  // growing the window past the phone layout shouldn't leave the menu "open"
  if (window.matchMedia) {
    const wide = window.matchMedia("(min-width: 761px)");
    const onChange = (ev) => { if (ev.matches) setNavOpen(false); };
    if (wide.addEventListener) wide.addEventListener("change", onChange);
    else if (wide.addListener) wide.addListener(onChange);
  }

  const game = document.body.dataset.game;
  if (game) {
    try {
      document.cookie = "gm_game=" + encodeURIComponent(game) + "; path=/; max-age=31536000; SameSite=Lax";
    } catch (err) { /* cookies blocked - the nav just won't remember the game */ }
  }
});


// Fog of war, shared by the GM's map page and the player view. Draws a dark
// layer over the whole map with soft-edged holes where the GM has revealed
// areas. All fog nodes carry class "fog-el" so the editor can strip them
// before saving or exporting the map. opacity < 1 gives the GM a see-through
// preview of what's hidden.
function applyFog(svg, state, opacity = 1) {
  const NS = "http://www.w3.org/2000/svg";
  svg.querySelectorAll(".fog-el").forEach((n) => n.remove());
  if (!state || !state.enabled) return;
  const vb = svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width
    ? svg.viewBox.baseVal
    : {x: 0, y: 0, width: parseFloat(svg.getAttribute("width")) || 1000, height: parseFloat(svg.getAttribute("height")) || 1000};
  const make = (tag, attrs) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  };
  const defs = make("defs", {"class": "fog-el"});
  const blur = make("filter", {id: "fog-blur", x: "-20%", y: "-20%", width: "140%", height: "140%"});
  blur.appendChild(make("feGaussianBlur", {stdDeviation: Math.max(vb.width, vb.height) / 220}));
  defs.appendChild(blur);
  const mask = make("mask", {id: "fog-mask", maskUnits: "userSpaceOnUse", x: vb.x, y: vb.y, width: vb.width, height: vb.height});
  mask.appendChild(make("rect", {x: vb.x, y: vb.y, width: vb.width, height: vb.height, fill: "white"}));
  const holes = make("g", {filter: "url(#fog-blur)", fill: "black"});
  (state.areas || []).forEach((a) => {
    if (a.shape === "rect") holes.appendChild(make("rect", {x: a.x, y: a.y, width: a.w, height: a.h, rx: 4}));
    else holes.appendChild(make("circle", {cx: a.x, cy: a.y, r: a.r}));
  });
  mask.appendChild(holes);
  defs.appendChild(mask);
  svg.appendChild(defs);
  svg.appendChild(make("rect", {
    "class": "fog-el", x: vb.x, y: vb.y, width: vb.width, height: vb.height,
    fill: "#0b0b10", opacity, mask: "url(#fog-mask)", "pointer-events": "none",
  }));
}


// Quick jump (Ctrl+K / Cmd+K, or the "Jump to..." button): type a few letters of
// a campaign, NPC, map, table, bookmark or tool and press Enter. Results come
// from /api/jump; everything is built with textContent so a name can't inject markup.
document.addEventListener("DOMContentLoaded", () => {
  const overlay = document.getElementById("jump-overlay");
  const input = document.getElementById("jump-input");
  const list = document.getElementById("jump-results");
  const button = document.getElementById("jump-btn");
  if (!overlay || !input || !list) return;

  let items = [];       // current results
  let active = -1;      // highlighted row
  let seq = 0;          // ignore answers that arrive after a newer request
  let timer = null;
  let returnFocus = null;

  const isOpen = () => !overlay.hidden;

  const setActive = (index, scroll = true) => {
    const rows = list.querySelectorAll("li[role=option]");
    if (!rows.length) { active = -1; input.removeAttribute("aria-activedescendant"); return; }
    active = (index + rows.length) % rows.length;
    rows.forEach((row, i) => {
      const on = i === active;
      row.classList.toggle("active", on);
      row.setAttribute("aria-selected", on ? "true" : "false");
      if (on) {
        input.setAttribute("aria-activedescendant", row.id);
        if (scroll) row.scrollIntoView({ block: "nearest" });
      }
    });
  };

  const render = (results, message) => {
    items = results || [];
    list.replaceChildren();
    if (message || !items.length) {
      list.appendChild(el("li", { class: "jump-empty muted" }, message || "No matches."));
      active = -1;
      input.removeAttribute("aria-activedescendant");
      return;
    }
    items.forEach((item, i) => {
      const link = el("a", { href: item.href, tabindex: "-1" }, [
        el("span", { class: "jump-label" }, item.label),
        item.detail ? el("span", { class: "jump-detail muted" }, item.detail) : null,
        el("span", { class: "jump-kind kind-" + item.kind }, item.kind_label),
      ]);
      const row = el("li", { role: "option", id: "jump-opt-" + i, "aria-selected": "false" }, [link]);
      row.addEventListener("mousemove", () => { if (active !== i) setActive(i, false); });
      row.addEventListener("click", () => close(false));
      list.appendChild(row);
    });
    setActive(0);
  };

  const search = async () => {
    const mine = ++seq;
    const params = new URLSearchParams({ q: input.value });
    if (document.body.dataset.game) params.set("game", document.body.dataset.game);
    try {
      const res = await fetch("/api/jump?" + params.toString());
      if (!res.ok) throw new Error("bad status");
      const data = await res.json();
      if (mine === seq) render(data.results);
    } catch (err) {
      if (mine === seq) render([], "Search isn't available right now.");
    }
  };

  const open = () => {
    if (isOpen()) return;
    returnFocus = document.activeElement;
    overlay.hidden = false;
    document.body.classList.add("jump-open");
    if (button) button.setAttribute("aria-expanded", "true");
    input.value = "";
    render([], "Loading...");
    input.focus();
    search();
  };

  function close(restore = true) {
    if (!isOpen()) return;
    overlay.hidden = true;
    document.body.classList.remove("jump-open");
    if (button) button.setAttribute("aria-expanded", "false");
    clearTimeout(timer);
    seq++;
    if (restore && returnFocus && returnFocus.focus) returnFocus.focus();
  }

  const go = (index) => {
    const item = items[index];
    if (!item) return;
    close(false);
    window.location.href = item.href;
  };

  if (button) button.addEventListener("click", open);

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(search, 120);
  });

  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      isOpen() ? close() : open();
      return;
    }
    if (!isOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
    else if (e.key === "Enter") { e.preventDefault(); if (active >= 0) go(active); }
    else if (e.key === "Tab") { e.preventDefault(); input.focus(); }  // focus stays in the box
  }, true);
});


// Tokens on a map, shared by the GM's map page and the player view. Draws one
// marker per token in a layer of class "token-el" (the editor strips those before
// saving or exporting, like the fog). The layer goes just under the fog, so a token
// standing in an area the players haven't seen yet stays hidden from them.
// tokens: [{id, label, x, y, color, size, hidden?}]; radius is one token's radius
// in the map's own units; a token's size (1-4) scales it up for big creatures.
function drawTokens(svg, tokens, radius, showLabels = true) {
  const NS = "http://www.w3.org/2000/svg";
  svg.querySelectorAll(".token-layer").forEach((n) => n.remove());
  if (!tokens || !tokens.length) return;
  const make = (tag, attrs) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  };
  const initials = (label) => {
    const words = String(label).trim().split(/\s+/).filter(Boolean);
    const text = words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2);
    return text.toUpperCase();
  };
  const ink = (hex) => {      // black or white, whichever reads better on the token colour
    const n = parseInt(String(hex).slice(1), 16) || 0;
    const lum = 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
    return lum > 150 ? "#111" : "#fff";
  };
  const layer = make("g", {"class": "token-el token-layer"});
  tokens.forEach((t) => {
    const r = radius * (1 + ((t.size || 1) - 1) * 0.6);
    const g = make("g", {
      "class": "token-el token" + (t.hidden ? " token-hidden" : ""),
      transform: `translate(${t.x},${t.y})`, "data-id": String(t.id),
    });
    const title = make("title", {});
    title.textContent = t.label + (t.hidden ? " (hidden from players)" : "");
    g.appendChild(title);
    g.appendChild(make("circle", {
      r, fill: t.color, stroke: t.hidden ? "#ffcf5c" : "#fff", "stroke-width": (r * 0.14).toFixed(2),
      "stroke-dasharray": t.hidden ? `${(r * 0.35).toFixed(2)} ${(r * 0.25).toFixed(2)}` : "none",
      opacity: t.hidden ? 0.6 : 1,
    }));
    const initialsEl = make("text", {
      "text-anchor": "middle", "dominant-baseline": "central", "font-size": (r * 0.95).toFixed(2),
      "font-family": "sans-serif", "font-weight": "bold", fill: ink(t.color), "pointer-events": "none",
    });
    initialsEl.textContent = initials(t.label);
    g.appendChild(initialsEl);
    if (showLabels) {
      const labelEl = make("text", {
        y: (r * 1.75).toFixed(2), "text-anchor": "middle", "font-size": (r * 0.75).toFixed(2),
        "font-family": "sans-serif", fill: "#fff", stroke: "#000", "stroke-width": (r * 0.2).toFixed(2),
        "paint-order": "stroke", "pointer-events": "none",
      });
      labelEl.textContent = t.label;
      g.appendChild(labelEl);
    }
    layer.appendChild(g);
  });
  const fog = svg.querySelector(".fog-el");
  svg.insertBefore(layer, fog || null);   // the fog stays on top
}
