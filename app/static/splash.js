window.bfSplash?.onState((s) => {
  const el = document.getElementById("state");
  el.textContent = s.text;
  el.className = s.error ? "state error" : "state";
  document.getElementById("actions").hidden = !s.error;
});
document.getElementById("close").addEventListener("click", () => window.bfSplash?.close());
document.getElementById("copy").addEventListener("click", () => window.bfSplash?.copyLog());
