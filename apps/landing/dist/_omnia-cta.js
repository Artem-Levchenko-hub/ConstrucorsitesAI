// Omnia.AI landing CTA handoff to the constructor.
(function () {
  var BASE = "https://constructor.lead-generator.ru";
  function go(path) {
    window.location.href = BASE + path + "?next=/projects";
  }
  document.addEventListener(
    "click",
    function (e) {
      var t = e.target && e.target.closest("a,button");
      if (!t) return;
      var x = (t.textContent || "").trim();
      if (/Начать бесплатно|Создать сайт|Попробовать|Начать сейчас/i.test(x)) {
        e.preventDefault();
        go("/register");
        return;
      }
      if (/^Войти$|Авторизоваться|^Вход$/i.test(x)) {
        e.preventDefault();
        go("/login");
      }
    },
    true,
  );
})();
