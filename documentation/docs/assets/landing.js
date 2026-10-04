/* Small, dependency-free interactions for the static landing page. */
(() => {
  "use strict";

  const menuButton = document.querySelector(".menu-toggle");
  const navigation = document.getElementById("site-navigation");
  const mobileViewport = window.matchMedia("(max-width: 760px)");

  const setMenu = (open) => {
    menuButton.setAttribute("aria-expanded", String(open));
    menuButton.setAttribute(
      "aria-label",
      open ? "Close navigation" : "Open navigation",
    );
    navigation.classList.toggle("is-open", open);
  };

  menuButton.addEventListener("click", () => {
    setMenu(menuButton.getAttribute("aria-expanded") !== "true");
  });
  navigation.addEventListener("click", (event) => {
    if (event.target.closest("a")) setMenu(false);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".site-header")) setMenu(false);
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      menuButton.getAttribute("aria-expanded") === "true"
    ) {
      setMenu(false);
      menuButton.focus();
    }
  });
  mobileViewport.addEventListener("change", () => setMenu(false));

  const tabs = [...document.querySelectorAll('.workflow-tabs [role="tab"]')];
  const selectTab = (tab) => {
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden =
        !selected;
    });
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      let nextIndex;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft")
        nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = tabs.length - 1;
      if (nextIndex === undefined) return;
      event.preventDefault();
      selectTab(tabs[nextIndex]);
      tabs[nextIndex].focus();
    });
  });

  const copyStatus = document.getElementById("copy-status");
  let statusTimeout;
  const announce = (message) => {
    window.clearTimeout(statusTimeout);
    copyStatus.textContent = message;
    copyStatus.classList.add("visible");
    statusTimeout = window.setTimeout(() => {
      copyStatus.classList.remove("visible");
    }, 3500);
  };
  document
    .querySelectorAll("[data-copy], [data-copy-target]")
    .forEach((button) => {
      button.addEventListener("click", async () => {
        const command =
          button.dataset.copy ||
          document.getElementById(button.dataset.copyTarget).textContent.trim();
        try {
          await navigator.clipboard.writeText(command);
          announce("Command copied.");
        } catch {
          announce("Couldn’t copy. Select the command to copy it manually.");
        }
      });
    });
})();
