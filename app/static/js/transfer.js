/* Transfer Funds page (wf-7). Conveniences only: the server checks everything again.
 *
 * 1. From/To menus: a native <select> can't put the account name on the left and its
 *    balance on the right, so each one is drawn as a Bootstrap dropdown. The <select>
 *    stays in the form, hidden, and is still what gets submitted; without JavaScript the
 *    page just shows the plain <select>.
 * 2. After a From choice, To options that can't work are greyed out: another bank account
 *    when From is a bank (money never moves bank to bank), or the same portfolio (R19).
 * 3. Remove buttons ask for confirmation first (R10.6).
 */
(function () {
  "use strict";

  function kind(value) {
    return (value || "").split(":")[0];
  }

  function twoColumns(name, detail) {
    var row = document.createElement("span");
    row.className = "d-flex justify-content-between gap-3";
    var left = document.createElement("span");
    left.className = "text-truncate";
    left.textContent = name;
    row.appendChild(left);
    if (detail) {
      var right = document.createElement("span");
      right.className = "text-body-secondary text-nowrap";
      right.textContent = detail;
      row.appendChild(right);
    }
    return row;
  }

  function AccountPicker(select) {
    var picker = this;
    this.select = select;
    this.items = [];

    var wrapper = document.createElement("div");
    wrapper.className = "dropdown account-picker";
    this.button = document.createElement("button");
    this.button.type = "button";
    this.button.id = select.id + "-picker";
    this.button.className = "form-select text-start";
    this.button.setAttribute("data-bs-toggle", "dropdown");
    this.button.setAttribute("aria-expanded", "false");
    var menu = document.createElement("ul");
    menu.className = "dropdown-menu w-100";

    Array.prototype.forEach.call(select.children, function (child) {
      var options = child.tagName === "OPTGROUP" ? child.children : [child];
      if (child.tagName === "OPTGROUP") {
        var header = document.createElement("li");
        header.innerHTML = '<h6 class="dropdown-header"></h6>';
        header.firstChild.textContent = child.label;
        menu.appendChild(header);
      }
      Array.prototype.forEach.call(options, function (option) {
        if (!option.value) {
          return; // the "Choose…" placeholder: the Clear button empties the choice
        }
        var li = document.createElement("li");
        var item = document.createElement("button");
        item.type = "button";
        item.className = "dropdown-item";
        item.appendChild(twoColumns(option.getAttribute("data-name"), option.getAttribute("data-detail")));
        item.addEventListener("click", function () {
          select.value = option.value;
          select.dispatchEvent(new Event("change"));
        });
        li.appendChild(item);
        menu.appendChild(li);
        picker.items.push({ option: option, item: item });
      });
    });

    wrapper.appendChild(this.button);
    wrapper.appendChild(menu);
    select.insertAdjacentElement("afterend", wrapper);
    select.classList.add("d-none");
    var label = document.querySelector('label[for="' + select.id + '"]');
    if (label) {
      label.htmlFor = this.button.id;
    }
    select.addEventListener("change", function () {
      picker.refresh();
    });
  }

  AccountPicker.prototype.refresh = function () {
    var chosen = this.select.options[this.select.selectedIndex];
    this.button.textContent = "";
    if (chosen && chosen.value) {
      this.button.appendChild(twoColumns(chosen.getAttribute("data-name"), chosen.getAttribute("data-detail")));
    } else {
      this.button.textContent = "Choose…";
    }
    this.button.classList.toggle("is-invalid", this.select.classList.contains("is-invalid"));
    this.items.forEach(function (entry) {
      entry.item.disabled = entry.option.disabled;
      entry.item.classList.toggle("active", entry.option.selected);
    });
  };

  document.addEventListener("DOMContentLoaded", function () {
    var from = document.getElementById("from_account");
    var to = document.getElementById("to_account");
    var pickers = [];

    function unusable(option) {
      if (option.value === "" || from.value === "") {
        return false;
      }
      if (kind(from.value) === "bank") {
        return kind(option.value) === "bank";
      }
      return option.value === from.value;
    }

    function sync() {
      Array.prototype.forEach.call(to.options, function (option) {
        option.disabled = unusable(option);
      });
      if (to.selectedIndex > 0 && to.options[to.selectedIndex].disabled) {
        to.selectedIndex = 0;
      }
      pickers.forEach(function (picker) {
        picker.refresh();
      });
    }

    if (from && to) {
      // The pickers sit inside the form's <fieldset>, so they are disabled whenever it is.
      [from, to].forEach(function (select) {
        pickers.push(new AccountPicker(select));
      });
      from.addEventListener("change", sync);
      from.form.addEventListener("reset", function () {
        setTimeout(sync, 0); // after the browser has reset the selects
      });
      sync();
    }

    Array.prototype.forEach.call(document.querySelectorAll("form[data-confirm]"), function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.getAttribute("data-confirm"))) {
          event.preventDefault();
        }
      });
    });
  });
})();
