// Account screens: the Users page (GM) and the "Players" card on a campaign page.
// Everything is built with createElement/textContent, so a name can't inject markup.

async function getJson(url) {
  const res = await fetch(url, {cache: "no-store"});
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || "Request failed");
  return json;
}

function formatWhen(stamp) {
  if (!stamp) return "never";
  const d = new Date(stamp.replace(" ", "T") + (stamp.endsWith("Z") ? "" : "Z"));
  return Number.isNaN(d.getTime()) ? stamp : d.toLocaleString([], {dateStyle: "medium", timeStyle: "short"});
}

// ---------------------------------------------------------------- Users page
function initUsersPage() {
  const setup = document.getElementById("setup-form");
  if (setup) {
    setup.addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = document.getElementById("setup-msg");
      try {
        await postForm("/api/users/first-gm", {
          username: document.getElementById("setup-username").value,
          password: document.getElementById("setup-password").value,
          confirm: document.getElementById("setup-confirm").value,
        });
        window.location.href = "/users";
      } catch (err) { msg.textContent = err.message; }
    });
    return;
  }
  const root = document.getElementById("users-root");
  if (!root) return;

  const meId = Number(root.dataset.me || 0);
  const table = document.getElementById("users-table");
  const msg = document.getElementById("users-msg");
  const tempBox = document.getElementById("temp-box");
  const gamesPanel = document.getElementById("games-panel");
  let data = {users: [], games: []};
  let openGamesFor = null;

  const say = (text) => { msg.textContent = text || ""; };
  const showTemp = (who, password) => {
    tempBox.replaceChildren(
      el("p", {style: "margin:0 0 0.4rem;"}, `Temporary password for ${who}:`),
      el("span", {class: "temp-pass"}, password),
      el("p", {class: "muted", style: "margin:0.4rem 0 0;"},
         "Shown once - pass it on now. They'll be asked to choose their own when they sign in."));
    tempBox.hidden = false;
  };
  const run = async (fn) => {
    try { await fn(); say(""); } catch (err) { say(err.message); }
  };

  async function load() {
    data = await getJson("/api/users");
    render();
  }

  function render() {
    const head = el("tr", {}, ["Name", "Role", "Status", "Campaigns", "Last sign-in", ""].map((t) => el("th", {style: "text-align:left;"}, t)));
    const rows = data.users.map((u) => {
      const isMe = u.id === meId;
      const role = el("select", {"aria-label": `Role for ${u.display_name}`}, [
        el("option", {value: "player"}, "Player"), el("option", {value: "gm"}, "GM")]);
      role.value = u.role;
      if (isMe) role.disabled = true;
      role.addEventListener("change", () => run(async () => {
        if (u.role === "player" && role.value === "gm" && !confirm(`Make ${u.display_name} a GM? They'll see everything, including your secrets.`)) { role.value = u.role; return; }
        await postForm(`/api/users/${u.id}/update`, {role: role.value});
        await load();
      }).then(() => { role.value = u.role; }));

      const status = [];
      if (u.disabled) status.push(el("span", {class: "badge bad"}, "Disabled"));
      if (u.must_change) status.push(el("span", {class: "badge", title: "Hasn't chosen their own password yet"}, "Temporary password"));
      if (isMe) status.push(el("span", {class: "badge good"}, "You"));

      const actions = [];
      if (u.role === "player") {
        const games = el("button", {type: "button", class: "secondary"}, "Games & library");
        games.addEventListener("click", () => { openGamesFor = openGamesFor === u.id ? null : u.id; renderGames(); });
        actions.push(games);
      }
      const reset = el("button", {type: "button", class: "secondary"}, "Reset password");
      reset.addEventListener("click", () => run(async () => {
        if (!confirm(`Reset the password for ${u.display_name}? This signs them out everywhere.`)) return;
        const res = await postForm(`/api/users/${u.id}/reset-password`, {});
        showTemp(u.display_name, res.temp_password);
        await load();
      }));
      actions.push(reset);
      if (!isMe) {
        const toggle = el("button", {type: "button", class: "secondary"}, u.disabled ? "Enable" : "Disable");
        toggle.addEventListener("click", () => run(async () => {
          await postForm(`/api/users/${u.id}/update`, {disabled: u.disabled ? 0 : 1});
          await load();
        }));
        const del = el("button", {type: "button", class: "secondary danger"}, "Delete");
        del.addEventListener("click", () => run(async () => {
          if (!confirm(`Delete ${u.display_name}? Their notes go too. This can't be undone.`)) return;
          await postForm(`/api/users/${u.id}/delete`, {});
          if (openGamesFor === u.id) openGamesFor = null;
          await load();
        }));
        actions.push(toggle, del);
      }
      return el("tr", {}, [
        el("td", {}, [el("strong", {}, u.display_name), el("div", {class: "muted"}, u.username)]),
        el("td", {}, role),
        el("td", {}, status.length ? status : el("span", {class: "muted"}, "Active")),
        el("td", {}, u.role === "gm" ? el("span", {class: "muted"}, "all") : String(u.campaigns)),
        el("td", {class: "muted"}, formatWhen(u.last_login)),
        el("td", {}, el("div", {class: "actions"}, actions)),
      ]);
    });
    table.replaceChildren(el("thead", {}, head), el("tbody", {}, rows));
    renderGames();
  }

  function renderGames() {
    const u = data.users.find((x) => x.id === openGamesFor);
    if (!u) { gamesPanel.hidden = true; gamesPanel.replaceChildren(); return; }
    const grid = el("div", {class: "game-grid"}, data.games.map((g) => {
      const access = u.games[g.key];
      const show = el("input", {type: "checkbox", "aria-label": `${g.name}: show this game`});
      show.checked = !!access;
      const lib = el("input", {type: "checkbox", "aria-label": `${g.name}: allow the library`});
      lib.checked = !!(access && access.library);
      lib.disabled = !access;
      const save = (which) => run(async () => {
        const res = await postForm(`/api/users/${u.id}/games`, {
          game_key: g.key, enabled: show.checked ? 1 : 0, library: show.checked && lib.checked ? 1 : 0});
        u.games = res.games;
        renderGames();
      }).catch(() => {});
      show.addEventListener("change", () => save("show"));
      lib.addEventListener("change", () => save("library"));
      return el("div", {class: "game-row"}, [
        el("span", {}, g.name),
        el("span", {class: "row", style: "gap:0.8rem; flex-wrap:nowrap;"}, [
          el("label", {class: "inline-check"}, [show, " Show"]),
          el("label", {class: "inline-check"}, [lib, " Library"]),
        ]),
      ]);
    }));
    gamesPanel.replaceChildren(
      el("h3", {style: "margin-top:0;"}, `Games for ${u.display_name}`),
      el("p", {class: "muted"},
         "Adding them to a campaign switches that game on for them. Tick Library only if you're happy for them to read that game's " +
         "books through GM Helper - it's your copy of the books, so check your licences first."),
      grid);
    gamesPanel.hidden = false;
  }

  document.getElementById("add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    run(async () => {
      const username = document.getElementById("add-username");
      const res = await postForm("/api/users", {
        username: username.value, display_name: document.getElementById("add-display").value,
        role: document.getElementById("add-role").value, password: document.getElementById("add-password").value,
      });
      if (res.temp_password) showTemp(res.user.display_name, res.temp_password);
      else { tempBox.hidden = true; say(`${res.user.display_name} added.`); }
      document.getElementById("add-form").reset();
      await load();
    });
  });

  load().catch((err) => say(err.message));
}

// ------------------------------------------------- Players card (campaign page)
function initPlayersCard(campaignId) {
  const body = document.getElementById("players-body");
  const msg = document.getElementById("players-msg");
  const notes = document.getElementById("player-notes");
  if (!body) return;
  const say = (text) => { msg.textContent = text || ""; };

  function draw(res) {
    if (!res.enabled) {
      body.replaceChildren(el("p", {class: "muted", style: "margin:0;"}, [
        "Accounts are off. ", el("a", {href: "/users"}, "Turn them on"), " to give players their own sign-in for this campaign."]));
      notes.hidden = true;
      return;
    }
    const chips = res.members.map((m) => {
      const x = el("button", {type: "button", class: "secondary", "aria-label": `Remove ${m.display_name}`}, "✕");
      x.addEventListener("click", async () => {
        try { draw(await postForm(`/api/campaigns/${campaignId}/members/${m.id}/remove`, {})); say(""); }
        catch (err) { say(err.message); }
      });
      return el("span", {class: "member-chip"}, [m.display_name + (m.disabled ? " (disabled)" : ""), x]);
    });
    const parts = [res.members.length ? el("div", {}, chips) : el("p", {class: "muted", style: "margin-top:0;"}, "No players yet.")];
    if (res.available.length) {
      const pick = el("select", {"aria-label": "Player to add"}, res.available.map((p) => el("option", {value: String(p.id)}, p.name)));
      const add = el("button", {type: "button"}, "Add to campaign");
      add.addEventListener("click", async () => {
        try { draw(await postForm(`/api/campaigns/${campaignId}/members`, {user_id: pick.value})); say(""); }
        catch (err) { say(err.message); }
      });
      parts.push(el("div", {class: "row"}, [pick, add]));
    } else {
      parts.push(el("p", {class: "muted", style: "margin:0;"}, [
        res.members.length ? "Everyone with a player account is already in. " : "No player accounts to add yet. ",
        el("a", {href: "/users"}, "Manage users")]));
    }
    body.replaceChildren(...parts);
    notes.hidden = false;
  }

  let notesLoaded = false;
  notes.addEventListener("toggle", async () => {
    if (!notes.open || notesLoaded) return;
    notesLoaded = true;
    const out = document.getElementById("player-notes-body");
    try {
      const res = await getJson(`/api/campaigns/${campaignId}/player-notes`);
      out.replaceChildren(...(res.notes.length ? res.notes.map((n) => el("div", {class: "subcard"}, [
        el("strong", {}, n.name),
        el("div", {class: "muted"}, n.updated_at ? `Updated ${formatWhen(n.updated_at)}` : "Nothing written yet"),
        n.body ? el("p", {class: "pre-wrap", style: "margin-bottom:0;"}, n.body) : null,
      ])) : [el("p", {class: "muted"}, "No players yet.")]));
    } catch (err) { out.replaceChildren(el("p", {class: "muted"}, err.message)); }
  });

  getJson(`/api/campaigns/${campaignId}/members`).then(draw).catch((err) => say(err.message));
}
