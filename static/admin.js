"use strict";

const $ = (id) => document.getElementById(id);
const toast = $("toast");
let toastTimer = null;
let config = null;        // working copy, shape of presets.yaml
let dirtyTts = new Set(); // preset ids whose audio must re-render on save
let presetsStatus = {};   // id -> has rendered audio
let voiceBaseline = "";   // tts settings at load/last render — changing them re-renders all

function showToast(msg, isError) {
  toast.textContent = msg;
  toast.classList.toggle("error", !!isError);
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 3000);
}

function adminHeaders(extra) {
  const h = Object.assign({}, extra);
  const pin = sessionStorage.getItem("holler_admin_pin");
  if (pin) h["X-Admin-Pin"] = pin;
  return h;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: adminHeaders(body ? { "Content-Type": "application/json" } : {}),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    const pin = prompt("Admin PIN:");
    if (pin === null) throw new Error("PIN required");
    sessionStorage.setItem("holler_admin_pin", pin);
    return api(method, path, body);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function slugify(label) {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "msg";
  let id = base, n = 2;
  while (config.presets.some((p) => p.id === id)) id = `${base}-${n++}`;
  return id;
}

// ---- Speakers section ----------------------------------------------------

function renderDeviceList() {
  const box = $("device-list");
  box.innerHTML = "";
  if (!config.devices.length) {
    box.innerHTML = '<p class="hint">No speakers yet — scan the network below.</p>';
    return;
  }
  for (const d of config.devices) {
    const row = document.createElement("div");
    row.className = "device-row";
    row.innerHTML = `
      <input type="checkbox" ${d.enabled ? "checked" : ""} aria-label="Broadcast to ${d.name}">
      <div class="info"><b>${d.name}</b><small>${d.host}</small></div>
      <button class="danger">Remove</button>`;
    row.querySelector("input").addEventListener("change", (e) => {
      d.enabled = e.target.checked;
    });
    row.querySelector("button").addEventListener("click", () => {
      if (!confirm(`Remove “${d.name}”?`)) return;
      config.devices = config.devices.filter((x) => x !== d);
      renderDeviceList();
    });
    box.appendChild(row);
  }
}

function renderScanResults(devices) {
  const box = $("scan-results");
  box.innerHTML = "";
  for (const d of devices) {
    const known = config.devices.some((x) => x.host === d.host || (d.uuid && x.uuid === d.uuid));
    const b = document.createElement("button");
    b.className = "device-pick";
    b.disabled = known;
    b.innerHTML = `${d.name}<small>${d.host} · ${d.model}${known ? " · already added" : ""}</small>`;
    b.addEventListener("click", () => {
      config.devices.push({ name: d.name, host: d.host, uuid: d.uuid, enabled: true });
      b.disabled = true;
      b.querySelector("small").textContent += " · added ✓";
      renderDeviceList();
    });
    box.appendChild(b);
  }
}

$("scan").addEventListener("click", async () => {
  $("scan").disabled = true;
  $("scan-status").textContent = "scanning ~10s…";
  try {
    const { devices } = await api("POST", "/api/admin/scan");
    $("scan-status").textContent = devices.length ? `${devices.length} found` : "none found — speaker plugged in?";
    renderScanResults(devices);
  } catch (e) {
    showToast(e.message, true);
    $("scan-status").textContent = "";
  }
  $("scan").disabled = false;
});

$("add-ip").addEventListener("click", () => {
  const host = prompt("Speaker IP address:");
  if (!host) return;
  const name = prompt("Name for this speaker:", host) || host;
  config.devices.push({ name, host: host.trim(), uuid: "", enabled: true });
  renderDeviceList();
});

// ---- Presets section -----------------------------------------------------

function presetCard(p) {
  const card = document.createElement("div");
  card.className = "preset-card";
  card.innerHTML = `
    <div class="row">
      <input type="text" class="fixed" style="width:64px;text-align:center;font-size:22px" value="${p.emoji || ""}" placeholder="📢" data-f="emoji">
      <input type="text" value="${p.label.replace(/"/g, "&quot;")}" placeholder="Button label" data-f="label">
    </div>
    <textarea placeholder="What the speaker says" data-f="tts">${p.tts || ""}</textarea>
    <div class="preset-actions">
      <button data-a="preview">▶ Preview</button>
      <button data-a="send">📢 Send to speaker</button>
      <button data-a="delete" class="danger">Delete</button>
      <span class="badge ${presetsStatus[p.id] === false ? "warn" : ""}">${presetsStatus[p.id] === false ? "no audio yet — Save renders it" : ""}</span>
    </div>`;

  card.querySelectorAll("[data-f]").forEach((el) => {
    el.addEventListener("input", () => {
      p[el.dataset.f] = el.value;
      if (el.dataset.f === "tts") dirtyTts.add(p.id);
    });
  });
  card.querySelector("[data-a=preview]").addEventListener("click", () => {
    new Audio(`/audio/${p.id}.wav?t=${Date.now()}`).play().catch(() =>
      showToast("No audio yet — Save first", true));
  });
  card.querySelector("[data-a=send]").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await fetch(`/api/broadcast/${p.id}`, {
        method: "POST",
        headers: config.security.pin ? { "X-Pin": config.security.pin } : {},
      }).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || "failed"); });
      showToast(`“${p.label}” sent ✓`);
    } catch (err) {
      showToast(err.message, true);
    }
    btn.disabled = false;
  });
  card.querySelector("[data-a=delete]").addEventListener("click", () => {
    if (!confirm(`Delete “${p.label}”?`)) return;
    config.presets = config.presets.filter((x) => x !== p);
    renderPresets();
  });
  return card;
}

function renderPresets() {
  const box = $("presets");
  box.innerHTML = "";
  config.presets.forEach((p) => box.appendChild(presetCard(p)));
}

$("add-preset").addEventListener("click", () => {
  const label = prompt("Button label (e.g. Laundry):");
  if (!label) return;
  const p = { id: slugify(label), label, emoji: "📢", tts: "" };
  config.presets.push(p);
  dirtyTts.add(p.id);
  presetsStatus[p.id] = false;
  renderPresets();
});

// ---- Save ----------------------------------------------------------------

$("volume").addEventListener("input", () => {
  $("volume-val").textContent = Math.round($("volume").value * 100) + "%";
});

function collect() {
  config.broadcast.volume = parseFloat($("volume").value);
  config.security.pin = $("pin").value.trim();
  config.security.admin_pin = $("admin-pin").value.trim();
  config.tts.engine = $("tts-engine").value;
  config.tts.voice = $("tts-voice").value.trim();
  const pm = $("tts-piper-model");
  if (pm.options.length) config.tts.piper_model = pm.value;
  return config;
}

$("save").addEventListener("click", async () => {
  const btn = $("save");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    await api("PUT", "/api/admin/config", collect());
    if (config.security.admin_pin) {
      sessionStorage.setItem("holler_admin_pin", config.security.admin_pin);
    }
    const voiceChanged = JSON.stringify(config.tts) !== voiceBaseline;
    const needRender = config.presets
      .filter((p) => voiceChanged || dirtyTts.has(p.id) || presetsStatus[p.id] === false)
      .map((p) => p.id);
    if (needRender.length) {
      btn.textContent = `Rendering audio (${needRender.length})…`;
      const { results } = await api("POST", "/api/admin/render", { ids: needRender });
      const failed = Object.entries(results).filter(([, r]) => !r.ok);
      if (failed.length) throw new Error(`Render failed: ${failed[0][0]} — ${failed[0][1].error}`);
      needRender.forEach((id) => { presetsStatus[id] = true; });
      dirtyTts.clear();
      voiceBaseline = JSON.stringify(config.tts);
      renderPresets();
    }
    showToast("Saved ✓");
  } catch (e) {
    showToast(e.message, true);
  }
  btn.disabled = false;
  btn.textContent = "Save changes";
});

// ---- Init ------------------------------------------------------------------

async function init() {
  try {
    const data = await api("GET", "/api/admin/config");
    config = data.config;
    presetsStatus = data.presets_status;
    renderDeviceList();
    $("volume").value = config.broadcast.volume;
    $("volume-val").textContent = Math.round(config.broadcast.volume * 100) + "%";
    $("pin").value = config.security.pin;
    $("admin-pin").value = config.security.admin_pin;
    $("tts-engine").value = config.tts.engine;
    $("tts-voice").value = config.tts.voice;
    if (data.piper_models.length) {
      const sel = $("tts-piper-model");
      sel.innerHTML = '<option value="">auto (first model found)</option>';
      for (const m of data.piper_models) {
        const o = document.createElement("option");
        o.value = m;
        o.textContent = m.split("/").pop().replace(".onnx", "");
        sel.appendChild(o);
      }
      sel.value = data.piper_models.includes(config.tts.piper_model) ? config.tts.piper_model : "";
      $("piper-voice-wrap").style.display = "";
    }
    voiceBaseline = JSON.stringify(config.tts);
    if (!data.engines.length) {
      showToast("No TTS engine on server — install piper or use recordings", true);
    }
    renderPresets();
  } catch (e) {
    showToast(e.message, true);
  }
}

init();
