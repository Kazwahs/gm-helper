// Campaign page: secrets & clues, progress clocks, and ties between NPCs and factions.
// Everything is drawn from /api/campaigns/<id>/world and rebuilt after each change.
(function () {
  const secretsBody = document.getElementById("secrets-body");
  const clocksBody = document.getElementById("clocks-body");
  const tiesBody = document.getElementById("ties-body");
  if (!secretsBody || !clocksBody || !tiesBody) return;
  const campaignId = secretsBody.closest("[data-campaign]").dataset.campaign;
  const SVGNS = "http://www.w3.org/2000/svg";
  const msgs = {
    secrets: document.getElementById("secrets-msg"),
    clocks: document.getElementById("clocks-msg"),
    ties: document.getElementById("ties-msg"),
  };
  let data = null;

  function say(card, text, bad) {
    const m = msgs[card];
    m.textContent = text || "";
    m.style.color = bad ? "var(--bad)" : "";
  }

  async function call(card, url, body) {
    try {
      const res = await postForm(url, body || {});
      say(card, "");
      return res;
    } catch (err) {
      say(card, err.message, true);
      return null;
    }
  }

  async function load() {
    try {
      const res = await fetch("/api/campaigns/" + campaignId + "/world");
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Request failed");
      data = json;
      render();
    } catch (err) {
      say("secrets", "Could not load: " + err.message, true);
    }
  }

  // ------------------------------------------------------------ shared bits

  function sessionLabel(s) {
    return "Session " + s.number + (s.title ? ": " + s.title : "");
  }

  function sessionSelect(selected, blank) {
    const sel = el("select", { "aria-label": "Session" });
    sel.appendChild(el("option", { value: "" }, blank));
    data.sessions.forEach((s) => {
      const o = el("option", { value: String(s.id) }, sessionLabel(s));
      if (String(selected) === String(s.id)) o.selected = true;
      sel.appendChild(o);
    });
    return sel;
  }

  function factionSelect(selected, blank) {
    const sel = el("select", { "aria-label": "Faction" });
    sel.appendChild(el("option", { value: "" }, blank));
    data.entities.filter((e) => e.kind === "faction").forEach((e) => {
      const o = el("option", { value: e.value.split(":")[1] }, e.name);
      if (String(selected) === e.value.split(":")[1]) o.selected = true;
      sel.appendChild(o);
    });
    return sel;
  }

  function textInput(value, label, max, onChange, cls) {
    const input = el("input", { type: "text", maxlength: String(max), "aria-label": label });
    if (cls) input.className = cls;
    input.value = value || "";
    input.addEventListener("change", () => onChange(input));
    return input;
  }

  // ---------------------------------------------------------------- secrets

  function secretRow(s) {
    const revealed = s.status === "revealed";
    const row = el("div", { class: "world-row secret-row" + (revealed ? " is-revealed" : ""), "data-id": String(s.id) });

    const toggle = el("button", { type: "button", class: revealed ? "secondary" : "", title: revealed ? "Put it back to hidden" : "The party found this out: note it in the session log" },
      revealed ? "Hide again" : "Reveal");
    toggle.addEventListener("click", async () => {
      toggle.disabled = true;
      let res;
      if (revealed) {
        res = await call("secrets", "/api/campaigns/secrets/" + s.id + "/hide");
      } else {
        const logSel = document.getElementById("log-session");   // the session being played
        res = await call("secrets", "/api/campaigns/secrets/" + s.id + "/reveal", { session_id: logSel ? logSel.value : "" });
        if (res) {
          say("secrets", "Revealed. A clue line was added to the session log.");
          document.dispatchEvent(new CustomEvent("campaign-log-changed"));
        }
      }
      if (res) await load(); else toggle.disabled = false;
    });

    const text = textInput(s.text, "Secret", 1000, async (input) => {
      const res = await call("secrets", "/api/campaigns/secrets/" + s.id + "/update", { field: "text", value: input.value });
      if (res) s.text = input.value; else input.value = s.text;
    }, "secret-text");

    const source = textInput(s.source, "Where the party can learn it", 200, async (input) => {
      const res = await call("secrets", "/api/campaigns/secrets/" + s.id + "/update", { field: "source", value: input.value });
      if (res) s.source = input.value; else input.value = s.source || "";
    }, "secret-source");
    source.placeholder = "Where they can learn it";

    const prepped = sessionSelect(s.session_id, "(no session)");
    prepped.title = "Session this was prepped for";
    prepped.addEventListener("change", async () => {
      const res = await call("secrets", "/api/campaigns/secrets/" + s.id + "/update", { field: "session_id", value: prepped.value });
      if (!res) prepped.value = s.session_id ? String(s.session_id) : "";
      else s.session_id = prepped.value ? Number(prepped.value) : null;
    });

    const del = el("button", { type: "button", class: "secondary", "aria-label": "Remove secret" }, "✕");
    del.addEventListener("click", async () => {
      if (!confirm("Remove this secret?")) return;
      if (await call("secrets", "/api/campaigns/secrets/" + s.id + "/delete")) await load();
    });

    row.append(toggle, text, source, prepped, del);
    if (revealed) {
      const where = s.revealed_number ? " in session " + s.revealed_number : "";
      row.appendChild(el("span", { class: "world-tag" }, "Revealed" + where));
    }
    return row;
  }

  function renderSecrets() {
    const hidden = data.secrets.filter((s) => s.status !== "revealed").length;
    const parts = [];
    if (!data.secrets.length) {
      parts.push(el("p", { class: "muted" }, "Nothing yet. Write the things the party could discover, then mark each one revealed at the table."));
    } else {
      parts.push(el("p", { class: "muted", style: "margin:0 0 0.4rem;" },
        hidden + " still hidden, " + (data.secrets.length - hidden) + " revealed."));
      parts.push(el("div", { class: "world-list" }, data.secrets.map(secretRow)));
    }

    const form = el("form", { class: "row", autocomplete: "off", style: "margin-top:0.75rem;" });
    const text = el("input", { type: "text", maxlength: "1000", placeholder: "A secret or clue the party could find", "aria-label": "New secret", style: "flex:1; min-width:220px;" });
    const source = el("input", { type: "text", maxlength: "200", placeholder: "Where (optional)", "aria-label": "Where the party can learn it", style: "width:12rem;" });
    const session = sessionSelect("", "(no session)");
    form.append(text, source, session, el("button", { type: "submit" }, "Add"));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!text.value.trim()) return;
      if (await call("secrets", "/api/campaigns/" + campaignId + "/secrets", { text: text.value, source: source.value, session_id: session.value })) await load();
    });
    parts.push(form);

    const importBtn = el("button", { type: "button", class: "secondary", style: "margin-top:0.5rem;",
      title: "Reads the Secrets & clues box of each session's prep, one secret per line" }, "Import from session prep");
    importBtn.addEventListener("click", async () => {
      importBtn.disabled = true;
      const res = await call("secrets", "/api/campaigns/" + campaignId + "/secrets/import");
      importBtn.disabled = false;
      if (!res) return;
      await load();
      say("secrets", res.added
        ? "Added " + res.added + " from your session prep" + (res.skipped ? " (" + res.skipped + " already listed)." : ".")
        : (res.skipped ? "Everything in your session prep is already listed." : "No lines in any session's Secrets & clues box."));
    });
    parts.push(importBtn);
    secretsBody.replaceChildren(...parts);
  }

  // ----------------------------------------------------------------- clocks

  function wedge(i, n, filled, onClick) {
    const cx = 32, cy = 32, r = 29;
    const a0 = (-90 + (i * 360) / n) * (Math.PI / 180);
    const a1 = (-90 + ((i + 1) * 360) / n) * (Math.PI / 180);
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", "M" + cx + " " + cy + " L" + x0.toFixed(2) + " " + y0.toFixed(2) +
      " A" + r + " " + r + " 0 0 1 " + x1.toFixed(2) + " " + y1.toFixed(2) + " Z");
    p.setAttribute("class", "clock-seg" + (i < filled ? " on" : ""));
    p.addEventListener("click", onClick);
    return p;
  }

  function clockRow(c) {
    const row = el("div", { class: "world-row clock-row" + (c.full ? " is-full" : ""), "data-id": String(c.id) });

    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 64 64");
    svg.setAttribute("class", "clock-svg");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", c.name + ": " + c.filled + " of " + c.segments);
    const move = async (delta) => {
      const res = await call("clocks", "/api/campaigns/clocks/" + c.id + "/advance", { delta: String(delta) });
      if (!res) return;
      await load();
      if (res.just_filled) say("clocks", "“" + c.name + "” is full. Time for it to happen.");
    };
    for (let i = 0; i < c.segments; i++) {
      // clicking a segment fills up to it; clicking the last filled one empties it
      svg.appendChild(wedge(i, c.segments, c.filled, () => move(i + 1 === c.filled ? -1 : i + 1 - c.filled)));
    }

    const name = textInput(c.name, "Clock name", 120, async (input) => {
      const res = await call("clocks", "/api/campaigns/clocks/" + c.id + "/update", { field: "name", value: input.value });
      if (res) c.name = input.value; else input.value = c.name;
    }, "clock-name");
    const note = textInput(c.note, "What happens when it fills", 1000, async (input) => {
      const res = await call("clocks", "/api/campaigns/clocks/" + c.id + "/update", { field: "note", value: input.value });
      if (res) c.note = input.value; else input.value = c.note || "";
    });
    note.placeholder = "What happens when it fills?";
    note.style.flex = "1";

    const faction = factionSelect(c.faction_id, "(no faction)");
    faction.addEventListener("change", async () => {
      const res = await call("clocks", "/api/campaigns/clocks/" + c.id + "/update", { field: "faction_id", value: faction.value });
      if (!res) faction.value = c.faction_id ? String(c.faction_id) : "";
      else c.faction_id = faction.value ? Number(faction.value) : null;
    });

    const minus = el("button", { type: "button", class: "secondary", "aria-label": "Roll back one segment" }, "−");
    const plus = el("button", { type: "button", "aria-label": "Fill one segment" }, "+");
    minus.disabled = c.filled <= 0;
    plus.disabled = c.full;
    minus.addEventListener("click", () => move(-1));
    plus.addEventListener("click", () => move(1));

    const count = el("span", { class: "clock-count" }, c.filled + " / " + c.segments);
    const seg = el("input", { type: "number", min: "2", max: "12", value: String(c.segments), "aria-label": "Segments", style: "width:4.2rem;" });
    seg.addEventListener("change", async () => {
      const res = await call("clocks", "/api/campaigns/clocks/" + c.id + "/update", { field: "segments", value: seg.value });
      if (res) await load(); else seg.value = String(c.segments);
    });
    const del = el("button", { type: "button", class: "secondary", "aria-label": "Remove clock" }, "✕");
    del.addEventListener("click", async () => {
      if (!confirm("Remove the clock “" + c.name + "”?")) return;
      if (await call("clocks", "/api/campaigns/clocks/" + c.id + "/delete")) await load();
    });

    const top = el("div", { class: "row clock-top" }, [name, faction]);
    const mid = el("div", { class: "row clock-mid" }, [minus, count, plus, el("label", { class: "muted", style: "margin-left:0.5rem;" }, "Segments"), seg, c.full ? el("span", { class: "world-tag" }, "Complete") : null, del]);
    row.append(svg, el("div", { class: "clock-body" }, [top, note, mid]));
    return row;
  }

  function renderClocks() {
    const parts = [];
    if (!data.clocks.length) {
      parts.push(el("p", { class: "muted" }, "No clocks yet. A clock is a threat on a timer: “the ritual completes”, “the watch finds the camp”. Fill a segment whenever the world moves."));
    } else {
      parts.push(el("div", { class: "world-list" }, data.clocks.map(clockRow)));
    }
    const form = el("form", { class: "row", autocomplete: "off", style: "margin-top:0.75rem;" });
    const name = el("input", { type: "text", maxlength: "120", placeholder: "New clock, e.g. The ritual completes", "aria-label": "Clock name", style: "flex:1; min-width:220px;" });
    const seg = el("select", { "aria-label": "Segments" }, [4, 6, 8, 10, 12].map((n) => {
      const o = el("option", { value: String(n) }, n + " segments");
      if (n === 6) o.selected = true;
      return o;
    }));
    const faction = factionSelect("", "(no faction)");
    form.append(name, seg, faction, el("button", { type: "submit" }, "Add clock"));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!name.value.trim()) return;
      if (await call("clocks", "/api/campaigns/" + campaignId + "/clocks", { name: name.value, segments: seg.value, faction_id: faction.value })) await load();
    });
    parts.push(form);
    clocksBody.replaceChildren(...parts);
  }

  // ------------------------------------------------------------------- ties

  function entitySelect(label, selected) {
    const sel = el("select", { "aria-label": label });
    sel.appendChild(el("option", { value: "" }, label));
    [["faction", "Factions"], ["npc", "People"]].forEach(([kind, title]) => {
      const group = data.entities.filter((e) => e.kind === kind);
      if (!group.length) return;
      const og = el("optgroup", { label: title });
      group.forEach((e) => {
        const o = el("option", { value: e.value }, e.name);
        if (e.value === selected) o.selected = true;
        og.appendChild(o);
      });
      sel.appendChild(og);
    });
    return sel;
  }

  function tieRow(t) {
    const row = el("div", { class: "world-row tie-row", "data-id": String(t.id) });
    row.appendChild(el("span", { class: "tie-phrase" }, t.phrase));
    if (t.note) row.appendChild(el("span", { class: "muted" }, " – " + t.note));
    const del = el("button", { type: "button", class: "secondary", style: "margin-left:auto;", "aria-label": "Remove tie" }, "✕");
    del.addEventListener("click", async () => {
      if (await call("ties", "/api/campaigns/relationships/" + t.id + "/delete")) await load();
    });
    row.appendChild(del);
    return row;
  }

  function renderTies() {
    const parts = [];
    if (data.relationships.length) {
      parts.push(el("div", { class: "world-list" }, data.relationships.map(tieRow)));
    } else {
      parts.push(el("p", { class: "muted" }, "No ties yet. Record who is allied with, owes, or hates whom, so you remember how a scene would ripple."));
    }
    if (data.entities.length < 2) {
      parts.push(el("p", { class: "muted" }, "Add at least two NPCs or factions above to tie them together."));
    } else {
      const form = el("form", { class: "row", autocomplete: "off", style: "margin-top:0.75rem;" });
      const a = entitySelect("Who…", "");
      const kind = el("select", { "aria-label": "Kind of tie" }, data.tie_kinds.map((k) => el("option", { value: k.value }, k.label)));
      const b = entitySelect("…whom", "");
      const note = el("input", { type: "text", maxlength: "300", placeholder: "Note (optional)", "aria-label": "Note", style: "flex:1; min-width:160px;" });
      form.append(a, kind, b, note, el("button", { type: "submit" }, "Add tie"));
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (await call("ties", "/api/campaigns/" + campaignId + "/relationships", { a: a.value, b: b.value, kind: kind.value, note: note.value })) await load();
      });
      parts.push(form);
    }
    tiesBody.replaceChildren(...parts);
  }

  function render() {
    renderSecrets();
    renderClocks();
    renderTies();
  }

  document.addEventListener("campaign-changed", load);   // a faction or NPC was removed
  window.refreshCampaignWorld = load;
  load();
})();
