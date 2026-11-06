/* Dark/light theme (D13, R2.8-R2.9; design "Colour theme").
 *
 * Pages are dark by default (data-bs-theme="dark" on <html>). This file is loaded in <head>,
 * before the page draws, so a saved "light" choice applies without a dark flash. It then
 * wires the navbar's theme button: sun icon in dark mode, moon icon in light mode.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "theme";
  var root = document.documentElement;

  function savedTheme() {
    try {
      var value = window.localStorage.getItem(STORAGE_KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (e) {
      return null; // storage blocked (private mode, strict settings): keep the default
    }
  }

  function saveTheme(theme) {
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch (e) {
      /* the switch still works for this page view */
    }
  }

  function currentTheme() {
    return root.getAttribute("data-bs-theme") === "light" ? "light" : "dark";
  }

  function applyTheme(theme) {
    root.setAttribute("data-bs-theme", theme);
  }

  var initial = savedTheme();
  if (initial) {
    applyTheme(initial);
  }

  function updateButtons(buttons) {
    var label = currentTheme() === "dark" ? "Switch to light mode" : "Switch to dark mode";
    buttons.forEach(function (button) {
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    // One button per layout: in the top bar on phones, beside Profile on wider screens.
    var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-theme-toggle]"));
    updateButtons(buttons);
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        var next = currentTheme() === "dark" ? "light" : "dark";
        applyTheme(next);
        saveTheme(next);
        updateButtons(buttons);
      });
    });
  });
})();
