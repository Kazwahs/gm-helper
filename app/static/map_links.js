// Linked maps card: show where this map leads, make a linked map (the city for a settlement,
// the level below a dungeon, a building inside a city), or link two maps that already exist.
(function () {
  const card = document.getElementById("links-card");
  if (!card) return;
  const mapId = card.dataset.map;
  const mapType = card.dataset.type;
  const list = document.getElementById("links-list");
  const actions = document.getElementById("links-actions");
  const setList = document.getElementById("links-settlements");
  const otherSel = document.getElementById("links-other");
  const kindSel = document.getElementById("links-kind");
  const addBtn = document.getElementById("links-add");
  const msg = document.getElementById("links-msg");
  const TYPE_NAMES = { dungeon: "Dungeon", interior: "Building", urban: "City", underground: "Caves", wilderness: "Wilderness", world: "World" };
  let data = null;

  function say(text, bad, href) {
    msg.textContent = "";
    msg.style.color = bad ? "var(--bad)" : "";
    if (text) msg.appendChild(document.createTextNode(text + (href ? " " : "")));
    if (href) msg.appendChild(el("a", { href }, "Open it"));
  }

  async function call(url, body, okText) {
    try {
      const res = await postForm("/api/maps/" + mapId + url, body || {});
      data = res;
      render();
      say(okText ? okText(res) : "", false, res.url);
      return res;
    } catch (err) {
      say(err.message, true);
      return null;
    }
  }

  function linkRow(l) {
    const row = el("div", { class: "link-row" });
    row.appendChild(el("span", { class: "link-role" }, l.role));
    row.appendChild(el("a", { href: "/maps/" + l.map_id }, l.title));
    row.appendChild(el("span", { class: "muted" }, " " + (TYPE_NAMES[l.map_type] || l.map_type) + (l.label ? " · " + l.label : "")));
    const un = el("button", { type: "button", class: "secondary", style: "margin-left:auto;" }, "Unlink");
    un.addEventListener("click", () => {
      if (confirm("Remove this link? Neither map is deleted.")) call("/links/" + l.id + "/delete", {});
    });
    row.appendChild(un);
    return row;
  }

  function actionButton(text, body) {
    const b = el("button", { type: "button", class: "secondary" }, text);
    b.addEventListener("click", async () => {
      b.disabled = true;
      say("Making the map…");
      await call("/links/create", body, (r) => "Made it and linked it.");
      b.disabled = false;
    });
    return b;
  }

  function render() {
    list.textContent = "";
    const all = [...data.links.out, ...data.links.in];
    if (!all.length) list.appendChild(el("p", { class: "muted", style: "margin:0;" }, "Not linked to any other map yet."));
    all.forEach((l) => list.appendChild(linkRow(l)));

    actions.textContent = "";
    if (["dungeon", "interior", "underground"].includes(mapType)) {
      actions.appendChild(actionButton("Add a level below", { kind: "below", map_type: mapType }));
      actions.appendChild(actionButton("Add a level above", { kind: "above", map_type: mapType }));
    }
    if (mapType === "urban") {
      actions.appendChild(actionButton("Add a building interior", { kind: "detail", map_type: "interior" }));
    }
    const hints = [];
    if (data.stairs.down.length) hints.push("stairs down: room " + data.stairs.down.join(", "));
    if (data.stairs.up.length) hints.push("stairs up: room " + data.stairs.up.join(", "));
    if (hints.length) actions.appendChild(el("span", { class: "muted" }, hints.join(" · ")));

    setList.textContent = "";
    if (data.settlements.length) {
      setList.appendChild(el("div", { class: "field-label" }, "Settlements on this map"));
      data.settlements.forEach((s, i) => {
        const row = el("div", { class: "link-row", id: "link-set-" + i });
        row.appendChild(el("span", {}, s.name));
        row.appendChild(el("span", { class: "muted" }, " " + s.tier));
        const existing = data.links.out.find((l) => l.label === s.name);
        if (existing) {
          row.appendChild(el("a", { href: "/maps/" + existing.map_id, style: "margin-left:auto;" }, "Open its map"));
        } else {
          const b = el("button", { type: "button", class: "secondary", style: "margin-left:auto;" }, "Make its map");
          b.addEventListener("click", async () => {
            b.disabled = true;
            say("Drawing " + s.name + "…");
            await call("/links/create", { kind: "detail", map_type: "urban", settlement: s.name }, () => "Made the map of " + s.name + ".");
            b.disabled = false;
          });
          row.appendChild(b);
        }
        setList.appendChild(row);
      });
    }

    otherSel.textContent = "";
    data.others.forEach((o) => otherSel.appendChild(el("option", { value: o.id }, o.title + " (" + (TYPE_NAMES[o.map_type] || o.map_type) + ")")));
    otherSel.disabled = addBtn.disabled = !data.others.length;
  }

  addBtn.addEventListener("click", () => {
    if (!otherSel.value) return;
    call("/links/add", { other_id: otherSel.value, kind: kindSel.value }, () => "Linked.");
  });

  // click a settlement on the map to find it in the list
  const frame = document.getElementById("map-frame");
  if (frame) {
    frame.addEventListener("click", (ev) => {
      if (["editing", "revealing", "placing-token"].some((c) => frame.classList.contains(c))) return;
      const g = ev.target.closest('.map-el[data-kind="settlement"]');
      if (!g || !data) return;
      const i = data.settlements.findIndex((s) => s.name === g.dataset.name);
      const row = i >= 0 && document.getElementById("link-set-" + i);
      if (!row) return;
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.add("key-flash");
      setTimeout(() => row.classList.remove("key-flash"), 1200);
    });
  }

  (async function load() {
    try {
      data = await (await fetch("/api/maps/" + mapId + "/links")).json();
      render();
    } catch (err) {
      say("Couldn't load the links.", true);
    }
  })();
})();
