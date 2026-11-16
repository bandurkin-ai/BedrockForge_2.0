window.bfSplash?.onState((s) => {
  const el = document.getElementById("state");
  el.textContent = s.text;
  el.className = s.error ? "state error" : "state";
});
