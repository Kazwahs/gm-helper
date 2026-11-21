// Fight builder on the Encounters page: the party, the foes, and a rough difficulty guide.
(function () {
  const card = document.getElementById("builder-card");
  if (!card) return;
  const game = card.dataset.game;
  const partyBody = document.getElementById("party-body");
  const foesBody = document.getElementById("foes-body");
  const hitsSel = document.getElementById("foe-hits");
  const savedSel = document.getElementById("saved-pick");
  const out = document.getElementById("guide-out");
  const msg = document.getElementById("builder-msg");
  const partyMsg = document.getElementById("party-msg");
  let profile = null;
  let saveTimer = null;

  function say(text, bad) {
    msg.textContent = text || "";
    msg.style.color = bad ? "var(--bad)" : "";
  }
  function sayParty(text, bad) {
    partyMsg.textContent = text || "";
    partyMsg.style.color = bad ? "var(--bad)" : "";
  }

  // ------------------------------------------------------------------ party

  function memberRow(m) {
    const row = el("tr", { class: "party-row" });
    const name = el("input", { type: "text", maxlength: "60", placeholder: "Name", "aria-label": "Name" });
    name.value = m.name || "";
    const hp = el("input", { type: "number", min: "1", max: "1000000", placeholder: profile.mdc ? "HP or M.D.C." : "Hit points", "aria-label": "Hit points", style: "width:7rem;" });
    hp.value = m.hp || "";
    const attacks = el("input", { type: "number", min: "1", max: "20", "aria-label": "Attacks per round", style: "width:4.5rem;" });
    attacks.value = m.attacks || profile.party_attacks;
    const mdc = el("input", { type: "checkbox", "aria-label": "Hit points are M.D.C." });
    mdc.checked = !!m.mdc;
    const del = el("button", { type: "button", class: "secondary", "aria-label": "Remove" }, "✕");
    del.addEventListener("click", () => { row.remove(); queueSave(); });
    [name, hp, attacks, mdc].forEach((input) => input.addEventListener("change", queueSave));
    row.append(el("td", {}, name), el("td", {}, hp));
    if (profile.mdc) row.appendChild(el("td", { style: "text-align:center;" }, mdc));
    row.append(el("td", {}, attacks), el("td", {}, del));
    row._read = () => ({ name: name.value, hp: hp.value, attacks: attacks.value, mdc: mdc.checked });
    return row;
  }

  function readParty() {
    return Array.from(partyBody.querySelectorAll(".party-row")).map((r) => r._read()).filter((m) => m.hp !== "" || m.name.trim());
  }

  function drawParty(members) {
    partyBody.replaceChildren(...members.map(memberRow));
  }

  function queueSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveParty, 500);
  }

  async function saveParty() {
    try {
      await postForm("/api/guide/party", { game, party: JSON.stringify(readParty()) });
      sayParty("Party saved.");
    } catch (err) {
      sayParty(err.message, true);
    }
  }

  document.getElementById("party-add").addEventListener("click", () => {
    partyBody.appendChild(memberRow({}));
  });

  document.getElementById("party-board").addEventListener("click", async () => {
    try {
      const res = await fetch("/api/guide/party/board?game=" + encodeURIComponent(game));
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Request failed");
      if (!json.party.length) {
        sayParty("No PCs with hit points are on the initiative board. Add them there, or type your party here.", true);
        return;
      }
      drawParty(json.party);
      await saveParty();
    } catch (err) {
      sayParty(err.message, true);
    }
  });

  // ------------------------------------------------------------------ rating

  function numberRow(label, value) {
    return el("tr", {}, [el("th", { scope: "row" }, label), el("td", {}, value)]);
  }

  function showResult(r) {
    const badge = el("span", { class: "band-badge band-" + r.band }, r.label);
    const top = el("div", { class: "row guide-top" }, [badge, el("strong", {}, r.band_text)]);
    const table = el("table", { class: "guide-table" }, [
      el("thead", {}, el("tr", {}, [el("th", {}, ""), el("th", {}, "Party"), el("th", {}, "Foes")])),
      el("tbody", {}, [
        el("tr", {}, [el("th", { scope: "row" }, "Fighters"), el("td", {}, String(r.party.members)), el("td", {}, String(r.foes.count))]),
        el("tr", {}, [el("th", { scope: "row" }, "Total " + r.unit), el("td", {}, r.party.hp.toLocaleString()), el("td", {}, r.foes.hp.toLocaleString())]),
        el("tr", {}, [el("th", { scope: "row" }, "Attacks per round"), el("td", {}, String(r.party.attacks)), el("td", {}, String(r.foes.attacks))]),
      ]),
    ]);
    const notes = el("ul", { class: "guide-notes" }, r.cautions.map((c) => el("li", {}, c)));
    out.replaceChildren(
      top,
      el("p", { style: "margin:0.5rem 0 0;" }, r.outcome),
      el("p", { class: "muted", style: "margin:0.2rem 0 0.6rem;" }, r.comparison),
      table,
      el("h4", { style: "margin:0.8rem 0 0.2rem;" }, "Keep in mind"),
      notes,
      el("p", { class: "muted", style: "margin:0.6rem 0 0;" }, r.rough + " Pressure " + r.pressure + ".")
    );
    out.hidden = false;
  }

  document.getElementById("rate-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    say("");
    try {
      const res = await postForm("/api/guide/assess", {
        game, body: foesBody.value, party: JSON.stringify(readParty()), foe_hits: hitsSel.value,
      });
      showResult(res);
    } catch (err) {
      out.hidden = true;
      say(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  // ------------------------------------------------------------- foes, saving

  savedSel.addEventListener("change", () => {
    const opt = savedSel.selectedOptions[0];
    if (opt && opt.dataset.body !== undefined) foesBody.value = opt.dataset.body;
    savedSel.value = "";
  });

  document.getElementById("save-enc").addEventListener("click", async () => {
    const name = document.getElementById("save-name");
    if (!name.value.trim()) { say("Type a name for the encounter first.", true); name.focus(); return; }
    try {
      await postForm("/api/initiative/encounters", { game, name: name.value, body: foesBody.value });
      say("");
      msg.textContent = "Saved. ";
      msg.appendChild(el("a", { href: "/tools/initiative" + (game === "_global" ? "" : "?game=" + encodeURIComponent(game)) }, "Open the initiative tracker to load it."));
      name.value = "";
    } catch (err) {
      say(err.message, true);
    }
  });

  // ------------------------------------------------------------------- start

  async function start() {
    try {
      const res = await fetch("/api/guide?game=" + encodeURIComponent(game));
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Request failed");
      profile = json.profile;
      hitsSel.replaceChildren(...json.options.foe_hits.map((o) => {
        const opt = el("option", { value: o.key }, o.label);
        if (o.key === json.options.default_foe_hits) opt.selected = true;
        return opt;
      }));
      savedSel.replaceChildren(el("option", { value: "" }, json.encounters.length ? "Start from a saved encounter…" : "(no saved encounters yet)"),
        ...json.encounters.map((enc) => {
          const o = el("option", { value: String(enc.id) }, enc.name);
          o.dataset.body = enc.body;
          return o;
        }));
      document.getElementById("party-mdc-head").hidden = !profile.mdc;
      document.getElementById("builder-profile").textContent = profile.label + ": the party is assumed to make " + profile.party_attacks +
        " attacks a round each and the foes " + profile.foe_attacks + ", unless you say otherwise.";
      drawParty(json.party.length ? json.party : [{}]);
    } catch (err) {
      say("Could not load the builder: " + err.message, true);
    }
  }
  start();
})();
