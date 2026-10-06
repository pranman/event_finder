/* All filters work without JavaScript; small screens start with results in view. */
(() => {
    const panel = document.querySelector("[data-mobile-collapse]");
    if (!panel || !window.matchMedia) return;
    const smallScreen = window.matchMedia("(max-width: 720px)");
    if (smallScreen.matches && !panel.querySelector(".filter-errors")) {
        panel.open = false;
    }
    smallScreen.addEventListener("change", (event) => {
        if (!event.matches) panel.open = true;
    });
})();
