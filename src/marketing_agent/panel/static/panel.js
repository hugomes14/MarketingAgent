// Bloqueio de duplo envio nos formularios simples (os do HTMX usam hx-disabled-elt).
// Um segundo clique nao pode repetir o POST: criaria outra sessao paga ou outra proposta.
// Nenhum botao do painel leva name/value, por isso desativa-los depois do evento nao perde dados.
(function () {
  "use strict";

  function isHtmxForm(form) {
    return form.hasAttribute("hx-post") || form.closest("[hx-boost]") !== null;
  }

  function setSubmitButtonsDisabled(form, isDisabled) {
    form.querySelectorAll('button, input[type="submit"]').forEach(function (button) {
      if (button.type === "submit") {
        button.disabled = isDisabled;
      }
    });
  }

  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || isHtmxForm(form)) {
      return;
    }
    if (form.dataset.submitted === "1") {
      event.preventDefault();
      return;
    }
    form.dataset.submitted = "1";
    // Num novo ciclo: o envio ja leu os campos e o botao desativado nao o cancela.
    window.setTimeout(function () {
      setSubmitButtonsDisabled(form, true);
    }, 0);
  });

  // Voltar atras com a cache de paginas: os formularios tem de voltar a ficar utilizaveis.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) {
      return;
    }
    document.querySelectorAll("form").forEach(function (form) {
      if (form.dataset.submitted === "1") {
        delete form.dataset.submitted;
        setSubmitButtonsDisabled(form, false);
      }
    });
  });
})();

// O lote atualiza-se sozinho enquanto ha publicacoes a gerar; nao pode apagar o que o
// responsavel esta a escrever (ou um painel de edicao aberto). Nesse caso salta-se esse ciclo.
(function () {
  "use strict";

  function isUserEditing(batch) {
    if (batch.querySelector("details[open]") !== null) {
      return true;
    }
    return Array.from(batch.querySelectorAll("textarea, input")).some(function (field) {
      return field.value !== field.defaultValue && field.type !== "hidden";
    });
  }

  document.body.addEventListener("htmx:beforeSwap", function (event) {
    const target = event.detail.target;
    if (target && target.id === "lote" && isUserEditing(target)) {
      event.detail.shouldSwap = false;
    }
  });
})();
