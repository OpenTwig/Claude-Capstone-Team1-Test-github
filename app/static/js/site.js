// Runs on every page. It does two things:
//
// 1. Light and dark mode. Pages start dark. The sun/moon button in the navigation bar switches,
//    and the choice is remembered in the browser (localStorage) for the next visit.
// 2. "Are you sure?" questions. A form with data-confirm="Question?" asks that question before
//    it is sent, and does nothing if you press Cancel.

// Bootstrap reads the theme from this attribute on the <html> tag: data-bs-theme="dark" or "light".
function getTheme() {
  return document.documentElement.getAttribute("data-bs-theme");
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-bs-theme", theme);
}

// Apply the saved choice straight away. This file loads before the page is drawn,
// so a light-mode user never sees a dark flash.
// (try/catch: some browsers block localStorage, for example in private mode.)
try {
  const savedTheme = localStorage.getItem("theme");
  if (savedTheme === "light" || savedTheme === "dark") {
    setTheme(savedTheme);
  }
} catch (error) {
  // Keep the default (dark).
}

function switchTheme() {
  let newTheme = "dark";
  if (getTheme() === "dark") {
    newTheme = "light";
  }
  setTheme(newTheme);
  try {
    localStorage.setItem("theme", newTheme);
  } catch (error) {
    // It still switches for this page; it just won't be remembered.
  }
}

function askBeforeSending(event) {
  const form = event.target;
  const question = form.getAttribute("data-confirm");
  if (!window.confirm(question)) {
    event.preventDefault(); // Cancel was pressed: don't send the form
  }
}

// The buttons and forms only exist once the page has loaded, so wait for that.
document.addEventListener("DOMContentLoaded", function () {
  for (const button of document.querySelectorAll("[data-theme-toggle]")) {
    button.addEventListener("click", switchTheme);
  }
  for (const form of document.querySelectorAll("form[data-confirm]")) {
    form.addEventListener("submit", askBeforeSending);
  }
});
