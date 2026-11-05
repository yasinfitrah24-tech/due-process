/* What HTMX polling needs on top of a swap: open sections stay open, focus stays put, a change of status is announced,
   and the evidence block opens once, when a level first appears. Nothing here reads or changes a case. */
(function () {
  var phone = window.matchMedia("(max-width: 719px)");
  var open = {};
  var focused = null;
  var levels = {};
  var revealedAt = {};
  var said = null;
  var settled = false; // false until the page's first look: a block already there on load has nothing to reveal

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
    all("[data-level][id]").forEach(function (block) {
      var now = block.getAttribute("data-level");
      var firstSeen = settled && !(block.id in levels);
      if ((levels[block.id] === "" || firstSeen) && now) revealedAt[block.id] = performance.now();
      levels[block.id] = now;
      var solid = block.querySelector(".solid");
      var elapsed = block.id in revealedAt ? performance.now() - revealedAt[block.id] : null;
      if (solid && elapsed !== null && elapsed < 1000) {
        solid.classList.add("reveal");
        solid.style.animationDelay = -elapsed + "ms"; // a swap in mid-animation carries on, it does not restart
      }
    });
  }

  /* A section that is always open on a wide screen is plain text there: no toggle, no stop for the keyboard. */
  function lock() {
    all("details[data-lock]").forEach(function (d) {
      var summary = d.querySelector(":scope > summary");
      if (phone.matches) {
        summary.removeAttribute("tabindex");
      } else {
        d.open = true;
        summary.setAttribute("tabindex", "-1");
      }
    });
  }

  function openTarget() {
    var target = location.hash && document.getElementById(location.hash.slice(1));
    if (target && target.tagName === "DETAILS") target.open = true;
  }

  /* One press, one request: the server refuses a repeat anyway, this shows that the press was heard. */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.hasAttribute("data-sent")) {
      event.preventDefault();
      return;
    }
    form.setAttribute("data-sent", "");
    var pressed = event.submitter || form.querySelector("button");
    var text = form.getAttribute("data-busy") || "Sending...";
    setTimeout(function () { // after the browser has read the pressed button's value
      Array.prototype.forEach.call(form.querySelectorAll("button"), function (b) { b.disabled = true; });
      form.setAttribute("aria-busy", "true");
      pressed.setAttribute("data-label", pressed.innerHTML);
      pressed.textContent = text;
      document.getElementById("live").textContent = text;
    }, 0);
  });

  /* Coming back with the Back button shows the page as it was left, so give the form back. */
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    all("form[data-sent]").forEach(function (form) {
      form.removeAttribute("data-sent");
      form.removeAttribute("aria-busy");
      Array.prototype.forEach.call(form.querySelectorAll("button"), function (b) {
        b.disabled = false;
        if (b.hasAttribute("data-label")) b.innerHTML = b.getAttribute("data-label");
      });
    });
  });

  document.addEventListener("click", function (event) {
    var summary = event.target.closest && event.target.closest("details[data-lock] > summary");
    if (summary && !phone.matches) event.preventDefault();
  });
  window.addEventListener("hashchange", openTarget);
  phone.addEventListener("change", lock);

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
    lock();
    if (focused) {
      var summary = document.querySelector("#" + focused + " > summary");
      if (summary) summary.focus();
    }
    reveal();
    announce();
  });

  fold();
  lock();
  openTarget();
  reveal();
  announce();
  settled = true;
  var notice = document.querySelector("[data-focus]");
  if (notice) notice.focus();
})();
