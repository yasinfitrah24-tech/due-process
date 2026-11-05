/* What HTMX polling needs on top of a swap: open sections stay open, focus stays put, a change of status is announced,
   and the evidence block opens once, when a level first appears. Nothing here reads or changes a case. */
(function () {
  var phone = window.matchMedia("(max-width: 719px)");
  var open = {};
  var focused = null;
  var level = null;
  var revealedAt = null;
  var said = null;

  function all(selector) {
    return Array.prototype.slice.call(document.querySelectorAll(selector));
  }

  function fold() {
    all("details[data-fold]").forEach(function (d) {
      if (!(d.id in open) && phone.matches) d.open = false;
    });
  }

  function announce() {
    var source = document.querySelector("[data-status]");
    var live = document.getElementById("live");
    if (!source || !live) return;
    var text = source.getAttribute("data-status");
    if (said !== null && text !== said) live.textContent = text;
    said = text;
  }

  function reveal() {
    var block = document.querySelector("[data-level]");
    if (!block) return;
    var now = block.getAttribute("data-level");
    if (level === "" && now) revealedAt = performance.now();
    level = now;
    var solid = block.querySelector(".solid");
    var elapsed = revealedAt === null ? null : performance.now() - revealedAt;
    if (solid && elapsed !== null && elapsed < 1000) {
      solid.classList.add("reveal");
      solid.style.animationDelay = -elapsed + "ms"; // a swap in mid-animation carries on, it does not restart
    }
  }

  document.addEventListener("htmx:beforeSwap", function () {
    all("details[id]").forEach(function (d) { open[d.id] = d.open; });
    var active = document.activeElement;
    focused = active && active.closest ? active.closest("details[id]") : null;
    focused = focused && active.tagName === "SUMMARY" ? focused.id : null;
  });

  document.addEventListener("htmx:afterSwap", function () {
    all("details[id]").forEach(function (d) {
      if (d.id in open) d.open = open[d.id];
    });
    fold();
    if (focused) {
      var summary = document.querySelector("#" + focused + " > summary");
      if (summary) summary.focus();
    }
    reveal();
    announce();
  });

  fold();
  reveal();
  announce();
})();
