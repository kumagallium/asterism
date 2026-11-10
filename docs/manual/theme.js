// マニュアルの外観はアプリと独立して、このブラウザに記憶する。
(() => {
  const key = "asterism.manual.theme";
  let theme = "dark";
  try {
    if (localStorage.getItem(key) === "light") theme = "light";
  } catch (_) {
    // 保存を許可しないブラウザでも切り替え自体は使える。
  }
  document.documentElement.dataset.theme = theme;
  document.addEventListener("DOMContentLoaded", () => {
    const select = document.getElementById("manual-theme");
    select.value = theme;
    select.closest(".theme-switch").hidden = false;
    select.addEventListener("change", () => {
      theme = select.value === "light" ? "light" : "dark";
      document.documentElement.dataset.theme = theme;
      try {
        localStorage.setItem(key, theme);
      } catch (_) {
        // 保存できないときも、このページには選択を反映する。
      }
    });
  });
})();
