/* Trade page (wf-2). Conveniences only: the server checks everything again, and every
 * part of the page also works without JavaScript (the symbol form then reloads the page).
 *
 * 1. Symbol lookup: asks /api/quote for the quote, fills the quote panel, and shows the
 *    ticket (the wireframe's second state) without reloading.
 * 2. Unit: the Amount box shows "$" for Dollars and "Shares" for Shares (A3).
 * 3. Portfolio: picking another one reloads the page for it.
 * 4. Forms marked data-submit-once disable their buttons after the first submit, so a
 *    double click can't send an order twice (design: Trading, Double submission).
 */
(function () {
  "use strict";

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function setText(selector, text) {
    var el = $(selector);
    if (el) {
      el.textContent = text;
    }
  }

  function showHistory(portfolioId, symbols) {
    var wrap = $("[data-history-wrap]");
    var list = $("[data-history]");
    if (!wrap || !list) {
      return;
    }
    list.textContent = "";
    symbols.forEach(function (symbol) {
      var link = document.createElement("a");
      link.href = "/trade?portfolio=" + encodeURIComponent(portfolioId) + "&symbol=" + encodeURIComponent(symbol);
      link.className = "me-2";
      link.setAttribute("data-history-symbol", "");
      link.textContent = symbol;
      list.appendChild(link);
    });
    wrap.hidden = symbols.length === 0;
  }

  function showQuote(form, quote) {
    var portfolioId = form.elements.portfolio.value;
    setText("[data-quote-name]", quote.company_name + " (" + quote.symbol + ")");
    setText("[data-quote-price]", quote.price_text);
    setText("[data-quote-change]", quote.change_text);
    setText("[data-quote-owned]", quote.shares_owned_text);
    setText("[data-quote-value]", quote.market_value_text);
    // Sell is greyed out when the portfolio holds none of this stock (R9.1).
    var sell = $("[data-sell]");
    var owned = quote.shares_owned !== null && Number(quote.shares_owned) > 0;
    sell.disabled = !owned;
    if (!owned && sell.selected) {
      $("#action").value = "";
    }
    var badge = $("[data-market]");
    badge.textContent = quote.market_text;
    badge.classList.toggle("text-bg-success", quote.market_open);
    badge.classList.toggle("text-bg-secondary", !quote.market_open);
    $("[data-quote]").hidden = false;
    var ticket = $("[data-ticket]");
    $("[data-ticket-symbol]").value = quote.symbol;
    ticket.hidden = false;
    form.elements.symbol.value = quote.symbol;
    showHistory(portfolioId, quote.history);
    // Keep the address in step, so a reload or the Back button shows the same stock.
    history.replaceState(null, "", "/trade?portfolio=" + encodeURIComponent(portfolioId) + "&symbol=" + encodeURIComponent(quote.symbol));
  }

  function symbolError(form, message) {
    var input = form.elements.symbol;
    input.classList.toggle("is-invalid", Boolean(message));
    setText('[data-error="symbol"]', message || "");
    if (message) {
      $("[data-quote]").hidden = true;
      $("[data-ticket]").hidden = true;
    }
  }

  function lookUp(form) {
    var symbol = form.elements.symbol.value.trim().toUpperCase();
    if (!symbol) {
      symbolError(form, "Enter a symbol.");
      return;
    }
    var url = "/api/quote/" + encodeURIComponent(symbol) + "?portfolio=" + encodeURIComponent(form.elements.portfolio.value);
    fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (response) {
        if (response.status === 404) {
          return response.json().then(function (body) { throw new Error(body.error || "Symbol not found"); });
        }
        if (!response.ok) {
          throw new Error("fallback");
        }
        return response.json();
      })
      .then(function (quote) {
        symbolError(form, "");
        showQuote(form, quote);
      })
      .catch(function (error) {
        if (error.message === "fallback") {
          form.submit(); // signed out or another problem: let the normal page handle it
        } else {
          symbolError(form, error.message);
        }
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var symbolForm = $("[data-symbol-form]");
    if (symbolForm && window.fetch) {
      symbolForm.addEventListener("submit", function (event) {
        event.preventDefault();
        lookUp(symbolForm);
      });
    }

    var unit = $("[data-unit]");
    var prefix = $("[data-amount-prefix]");
    if (unit && prefix) {
      unit.addEventListener("change", function () {
        prefix.textContent = unit.value === "Shares" ? "Shares" : "$";
      });
    }

    var portfolioForm = $("[data-portfolio-form]");
    if (portfolioForm) {
      $("[data-portfolio-go]", portfolioForm).classList.add("d-none");
      portfolioForm.elements.portfolio.addEventListener("change", function () {
        portfolioForm.submit();
      });
    }

    Array.prototype.forEach.call(document.querySelectorAll("form[data-submit-once]"), function (form) {
      form.addEventListener("submit", function (event) {
        if (form.dataset.submitted) {
          event.preventDefault();
          return;
        }
        form.dataset.submitted = "yes";
        Array.prototype.forEach.call(form.querySelectorAll("button[type=submit]"), function (button) {
          button.disabled = true;
        });
      });
    });
  });
})();
