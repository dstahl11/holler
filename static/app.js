"use strict";

const grid = document.getElementById("grid");
const toast = document.getElementById("toast");
const statusEl = document.getElementById("status");
const pinOverlay = document.getElementById("pin-overlay");
const pinDots = document.querySelectorAll("#pin-dots i");
const pinPad = document.getElementById("pin-pad");

let pinRequired = false;
let pinBuffer = "";
let toastTimer = null;

function haptic(ms) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

function showToast(msg, isError) {
  toast.textContent = msg;
  toast.classList.toggle("error", !!isError);
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
}

function getPin() {
  return sessionStorage.getItem("holler_pin") || "";
}

// ---- PIN pad ----------------------------------------------------------

function buildPinPad() {
  const keys = ["1","2","3","4","5","6","7","8","9","","0","⌫"];
  for (const k of keys) {
    const b = document.createElement("button");
    b.textContent = k;
    if (k === "") b.className = "blank";
    else b.addEventListener("click", () => pinKey(k));
    pinPad.appendChild(b);
  }
}

function pinKey(k) {
  haptic(10);
  if (k === "⌫") pinBuffer = pinBuffer.slice(0, -1);
  else if (pinBuffer.length < 4) pinBuffer += k;
  pinDots.forEach((d, i) => d.classList.toggle("filled", i < pinBuffer.length));
  if (pinBuffer.length === 4) {
    sessionStorage.setItem("holler_pin", pinBuffer);
    pinBuffer = "";
    setTimeout(() => {
      pinDots.forEach((d) => d.classList.remove("filled"));
      pinOverlay.classList.remove("show");
    }, 150);
  }
}

function requirePin() {
  sessionStorage.removeItem("holler_pin");
  pinBuffer = "";
  pinDots.forEach((d) => d.classList.remove("filled"));
  pinOverlay.classList.add("show");
}

// ---- Presets ----------------------------------------------------------

function setButtonState(btn, state, subText) {
  btn.classList.remove("sending", "sent", "failed");
  if (state) btn.classList.add(state);
  const sub = btn.querySelector(".sub");
  sub.innerHTML = "";
  if (state === "sending") {
    const s = document.createElement("div");
    s.className = "spinner";
    sub.appendChild(s);
  } else {
    sub.textContent = subText || "";
  }
}

async function sendPreset(btn, preset) {
  if (btn.classList.contains("sending")) return;
  haptic(30);
  setButtonState(btn, "sending");

  let ok = false, detail = "";
  try {
    const res = await fetch(`/api/broadcast/${encodeURIComponent(preset.id)}`, {
      method: "POST",
      headers: pinRequired ? { "X-Pin": getPin() } : {},
    });
    if (res.status === 401) {
      setButtonState(btn, null);
      requirePin();
      showToast("PIN required", true);
      return;
    }
    const body = await res.json().catch(() => ({}));
    ok = res.ok;
    detail = body.detail || "";
  } catch (e) {
    detail = "Network error";
  }

  if (ok) {
    haptic([20, 40, 20]);
    setButtonState(btn, "sent", "Sent ✓");
    showToast(`“${preset.label}” sent ✓`);
  } else {
    haptic(200);
    setButtonState(btn, "failed", "Failed — tap to retry");
    showToast(detail || "Failed to reach speaker", true);
  }
  setTimeout(() => setButtonState(btn, null), 4000);
}

function render(data) {
  pinRequired = data.pin_required;
  if (pinRequired && !getPin()) requirePin();
  statusEl.textContent = data.dry_run ? "dry run" : "";

  grid.innerHTML = "";
  for (const preset of data.presets) {
    const btn = document.createElement("button");
    btn.className = "preset";
    btn.disabled = !preset.ready;
    btn.innerHTML =
      `<div class="emoji">${preset.emoji || "📢"}</div>` +
      `<div>${preset.label}</div>` +
      `<div class="sub">${preset.ready ? "" : "no audio"}</div>`;
    btn.addEventListener("click", () => sendPreset(btn, preset));
    grid.appendChild(btn);
  }
}

async function init() {
  buildPinPad();
  try {
    const res = await fetch("/api/presets");
    render(await res.json());
  } catch (e) {
    showToast("Can't reach Holler server", true);
  }
}

// Service worker only registers on https/localhost (browser rule); over plain
// LAN http the app still works fine as a home-screen shortcut.
if ("serviceWorker" in navigator &&
    (location.protocol === "https:" || location.hostname === "localhost")) {
  navigator.serviceWorker.register("/sw.js");
}

init();
