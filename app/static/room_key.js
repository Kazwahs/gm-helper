// Room key card on the map page: generate the key, edit entries (saved as you go),
// flesh one out with the AI model, and jump from a room on the map to its entry.
(function () {
  const card = document.getElementById("key-card");
  if (!card) return;
  const mapId = card.dataset.map;
  const list = document.getElementById("key-list");
  const msg = document.getElementById("key-msg");
  const genBtn = document.getElementById("key-generate");
  const clearBtn = document.getElementById("key-clear");
  const printLink = document.getElementById("key-print");
  const tableSel = document.getElementById("key-table");
  let kinds = {};
  let aiOn = false;

  const FIELDS = [
    ["description", "What the players see", 2],
    ["contents", "What is here (creatures, trap, puzzle)", 2],
    ["treasure", "Treasure", 1],
    ["secrets", "Secret and locked doors", 2],
    ["notes", "Your notes", 2],
  ];

  function say(text, bad) {
    msg.textContent = text || "";
    msg.style.color = bad ? "var(--bad)" : "";
  }

  function entryNode(e) {
    const details = el("details", { class: "key-entry", id: "key-room-" + e.room });
    const kindTag = el("span", { class: "key-kind" }, kinds[e.kind] || e.kind);
    const titleSpan = el("span", { class: "key-title" }, e.title);
    details.appendChild(el("summary", {}, [el("strong", {}, e.room + ". "), titleSpan, " ", kindTag]));

    const meta = [];
    if (e.size) meta.push(e.size + " squares");
    if (e.exits) meta.push("Exits: " + e.exits);
    if (meta.length) details.appendChild(el("p", { class: "muted", style: "margin:0.3rem 0;" }, meta.join(" · ")));

    const save = async (name, value, input) => {
      try {
        const res = await postForm("/api/maps/" + mapId + "/key/" + e.room, { [name]: value });
        Object.assign(e, res.entry);
        titleSpan.textContent = e.title;
        kindTag.textContent = kinds[e.kind] || e.kind;
        say("Saved.");
      } catch (err) {
        say(err.message, true);
        if (input) input.focus();
      }
    };

    const top = el("div", { class: "row" });
    const titleInput = el("input", { type: "text", value: e.title, maxlength: "200", "aria-label": "Room " + e.room + " name" });
    titleInput.style.minWidth = "14rem";
    titleInput.addEventListener("change", () => save("title", titleInput.value, titleInput));
    const kindSel = el("select", { "aria-label": "Room " + e.room + " type" });
    Object.entries(kinds).forEach(([value, label]) => {
      const o = el("option", { value }, label);
      if (value === e.kind) o.selected = true;
      kindSel.appendChild(o);
    });
    kindSel.addEventListener("change", () => save("kind", kindSel.value));
    top.append(titleInput, kindSel);
    details.appendChild(top);

    const areas = {};
    FIELDS.forEach(([name, label, rows]) => {
      const ta = el("textarea", { rows: String(rows), maxlength: "4000", "aria-label": label });
      ta.value = e[name] || "";
      ta.addEventListener("change", () => save(name, ta.value, ta));
      areas[name] = ta;
      details.appendChild(el("div", { class: "field-group", style: "margin:0.4rem 0 0;" },
        [el("label", { class: "field-label" }, label), ta]));
    });

    if (aiOn) {
      const aiBtn = el("button", { type: "button", class: "secondary", style: "margin-top:0.5rem;" }, "Flesh out with AI");
      aiBtn.addEventListener("click", async () => {
        aiBtn.disabled = true;
        say("Asking the model…");
        try {
          const res = await postForm("/api/maps/" + mapId + "/key/" + e.room + "/ai", {});
          Object.assign(e, res.entry);
          FIELDS.forEach(([name]) => { areas[name].value = e[name] || ""; });
          say("Room " + e.room + " rewritten. Check it before you use it.");
        } catch (err) {
          say(err.message, true);
        } finally {
          aiBtn.disabled = false;
        }
      });
      details.appendChild(aiBtn);
    }
    return details;
  }

  function render(entries) {
    list.textContent = "";
    entries.forEach((e) => list.appendChild(entryNode(e)));
    clearBtn.hidden = printLink.hidden = !entries.length;
    genBtn.textContent = entries.length ? "Generate a new key" : "Generate key";
  }

  async function load() {
    try {
      const res = await fetch("/api/maps/" + mapId + "/key");
      const data = await res.json();
      kinds = data.kinds || {};
      aiOn = !!data.ai;
      render(data.entries || []);
    } catch (err) {
      say("Couldn't load the key.", true);
    }
  }

  genBtn.addEventListener("click", async () => {
    if (list.children.length && !confirm("Replace the whole key, including anything you have written, with a new one?")) return;
    genBtn.disabled = true;
    say("Generating…");
    try {
      const res = await postForm("/api/maps/" + mapId + "/key/generate", { creature_table: tableSel ? tableSel.value : "" });
      kinds = res.kinds || kinds;
      aiOn = !!res.ai;
      render(res.entries);
      say("Key generated.");
    } catch (err) {
      say(err.message, true);
    } finally {
      genBtn.disabled = false;
    }
  });

  clearBtn.addEventListener("click", async () => {
    if (!confirm("Delete the whole room key?")) return;
    try {
      const res = await postForm("/api/maps/" + mapId + "/key/clear", {});
      render(res.entries);
      say("Key cleared.");
    } catch (err) {
      say(err.message, true);
    }
  });

  // click a room on the map (when no other tool is using clicks) to open its entry
  const frame = document.getElementById("map-frame");
  if (frame) {
    frame.addEventListener("click", (ev) => {
      if (["editing", "revealing", "placing-token"].some((c) => frame.classList.contains(c))) return;
      if (ev.target.closest(".token")) return;
      const room = ev.target.closest('.map-el[data-kind="room"]');
      if (!room) return;
      const target = document.getElementById("key-room-" + room.dataset.room);
      if (!target) return;
      list.querySelectorAll("details[open]").forEach((d) => { if (d !== target) d.open = false; });
      target.open = true;
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("key-flash");
      setTimeout(() => target.classList.remove("key-flash"), 1200);
    });
  }

  load();
})();
