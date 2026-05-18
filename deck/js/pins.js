/* Auto-injects a real DOM corner badge into every [data-pin] element.
   We do this instead of a `::before { content: attr(data-pin) }` pseudo
   because html-to-image (used by save_screenshot/PPTX export) does not
   render attr() inside pseudo-element content. Real DOM nodes always
   serialize correctly. */
(function () {
  function inject() {
    document.querySelectorAll("[data-pin]").forEach(function (el) {
      if (el.querySelector(":scope > .corner-pin")) return;
      var pin = document.createElement("span");
      pin.className = "corner-pin";
      pin.textContent = el.getAttribute("data-pin");
      el.insertBefore(pin, el.firstChild);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else {
    inject();
  }
  window.addEventListener("load", inject);
  window.__renderPins = inject;
})();
