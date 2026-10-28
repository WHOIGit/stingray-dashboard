// Close open "More" popovers when clicking elsewhere on the page.
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.more-menu[open]").forEach((menu) => {
    if (!menu.contains(event.target) && document.body.contains(event.target)) {
      menu.removeAttribute("open");
    }
  });
});
