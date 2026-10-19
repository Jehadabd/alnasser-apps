// تحسينات صغيرة فقط؛ الموقع يعمل كاملاً بدون هذا الملف.
// لا طلبات شبكة، لا تخزين، لا تتبّع.
(function () {
  "use strict";

  // 1) زر «نسخ البصمة»
  if (navigator.clipboard && window.isSecureContext) {
    document.querySelectorAll("button.copy[data-copy]").forEach(function (btn) {
      var target = document.getElementById(btn.getAttribute("data-copy"));
      if (!target) return;
      btn.hidden = false;
      btn.addEventListener("click", function () {
        navigator.clipboard.writeText(target.textContent.trim()).then(function () {
          btn.textContent = "نُسخت البصمة";
          setTimeout(function () { btn.textContent = "نسخ البصمة"; }, 2500);
        });
      });
    });
  }

  // 2) زر جهازك أولاً: على أندرويد يتقدّم زر أندرويد، وعلى الحاسبة زر ويندوز
  var ua = navigator.userAgent || "";
  var mine = /Android/i.test(ua) ? "android" : (/Windows/i.test(ua) ? "windows" : null);
  if (!mine) return;
  document.querySelectorAll(".btns, .vouchers").forEach(function (box) {
    var el = box.querySelector('[data-platform="' + mine + '"]');
    if (el && box.firstElementChild !== el) box.insertBefore(el, box.firstElementChild);
    if (el && el.classList.contains("btn")) {
      var tag = document.createElement("span");
      tag.className = "fit";
      tag.textContent = "مناسب لجهازك";
      el.appendChild(tag);
    }
  });
})();
