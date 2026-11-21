// "Export to a virtual tabletop": the server works out the walls and doors and the exact
// pixel size; this renders the map image at that size and downloads the finished file.
(function () {
  const card = document.getElementById("vtt-card");
  if (!card) return;
  const mapId = card.dataset.map;
  const ppgSel = document.getElementById("vtt-ppg");
  const btn = document.getElementById("vtt-download");
  const msg = document.getElementById("vtt-msg");
  const note = document.getElementById("vtt-note");
  const MAX_SIDE = 12000; // browsers refuse canvases much bigger than this

  function say(text, bad) {
    msg.textContent = text || "";
    msg.style.color = bad ? "var(--bad)" : "";
  }

  // The map as the players should see it: no GM-only marks, selection boxes, fog or tokens.
  function cleanSvg(width, height) {
    const root = document.querySelector("#map-frame svg");
    const clone = root.cloneNode(true);
    clone.querySelectorAll(".map-el-selection-box, .fog-el, .token-el, .gm-only").forEach((n) => n.remove());
    clone.querySelectorAll(".map-el.selected").forEach((n) => n.classList.remove("selected"));
    clone.setAttribute("width", width);
    clone.setAttribute("height", height);
    return new XMLSerializer().serializeToString(clone);
  }

  function renderPng(width, height) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = width;
          canvas.height = height;
          canvas.getContext("2d").drawImage(img, 0, 0, width, height);
          resolve(canvas.toDataURL("image/png").split(",")[1]);
        } catch (err) {
          reject(err);
        }
      };
      img.onerror = () => reject(new Error("The browser couldn't draw the map."));
      img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(cleanSvg(width, height))));
    });
  }

  async function run() {
    btn.disabled = true;
    try {
      say("Working out the walls…");
      const res = await fetch("/api/maps/" + mapId + "/vtt?ppg=" + encodeURIComponent(ppgSel.value));
      const info = await res.json();
      if (!res.ok) throw new Error(info.error || "Export failed.");
      note.textContent = info.note;
      if (Math.max(info.image_width, info.image_height) > MAX_SIDE) {
        throw new Error("That would be a " + info.image_width + " by " + info.image_height +
          " pixel image, which is too big for a browser. Choose fewer pixels per square.");
      }
      say("Drawing the image (" + info.image_width + " × " + info.image_height + ")…");
      const png = await renderPng(info.image_width, info.image_height);
      info.document.image = png;
      const blob = new Blob([JSON.stringify(info.document)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = info.filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      say("Done: " + info.filename);
    } catch (err) {
      say(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", run);
})();
