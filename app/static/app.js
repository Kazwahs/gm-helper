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
