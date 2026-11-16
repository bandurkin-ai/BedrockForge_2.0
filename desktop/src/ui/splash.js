"use strict";
const $ = (id) => document.getElementById(id);
let typed = false;
let countdown = null;

$("server").addEventListener("input", () => (typed = true));
$("server").addEventListener("keydown", (e) => { if (e.key === "Enter") $("save").click(); });
$("close").addEventListener("click", () => window.bfSplash.quit());
$("retry").addEventListener("click", () => window.bfSplash.retry());
$("save").addEventListener("click", async () => {
  const r = await window.bfSplash.setServer($("server").value);
  if (!r.ok) setStatus(r.error, true);
  else typed = false;
});

function setStatus(text, error) {
  $("status").textContent = text;
  $("status").className = error ? "status err" : "status";
}

window.bfSplash.onState((s) => {
  clearInterval(countdown);
  $("version").textContent = `версия ${s.version}`;
  if (!typed) $("server").value = s.server;
  $("unavailable").classList.toggle("on", s.phase === "unavailable");
  $("bar").style.visibility = s.phase === "unavailable" ? "hidden" : "visible";
  if (s.phase === "connecting") setStatus(`Подключение к серверу ${s.server}…`);
  else if (s.phase === "starting") {
    setStatus("Сервер на связи. Загрузка проектов…");
    if (s.warning) {
      const w = document.createElement("div");
      w.className = "warn";
      w.textContent = s.warning;
      $("status").appendChild(w);
    }
  } else if (s.phase === "unavailable") {
    let left = Math.round(s.retryMs / 1000);
    const show = () => setStatus(`Сервер недоступен: ${s.error}. Запустите Docker Desktop и BedrockForge.bat — подключусь сам (через ${left}\u00a0с).`, true);
    show();
    countdown = setInterval(() => { left = Math.max(0, left - 1); show(); }, 1000);
  }
});
window.bfSplash.ready();
