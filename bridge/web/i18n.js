/* System strings only. User titles, messages, filenames and custom artwork remain verbatim. */
(() => {
  let language = "zh-CN";
  try { language = localStorage.getItem("dotii-language") || (navigator.language.startsWith("zh") ? "zh-CN" : "en"); } catch {}
  let catalog = {};
  let reverse = {};
  const ready = fetch("/translations.json", {cache:"no-store"}).then(response => {
    if (!response.ok) throw new Error("Translation catalog unavailable");
    return response.json();
  }).then(data => {
    catalog = data;
    reverse = Object.fromEntries(Object.entries(catalog).map(([zh,en]) => [en,zh]));
    apply();
  });
  function t(text) {
    if (typeof text !== "string") return text;
    const source = reverse[text] || text;
    return language === "en" ? (catalog[source] || text) : source;
  }
  function tr(strings, ...values) {
    const key = strings.reduce((result,part,index) => result + part + (index < values.length ? `{${index}}` : ""), "");
    return t(key).replace(/\{(\d+)\}/g, (_,index) => String(values[Number(index)]));
  }
  function localize(value) {
    if (!value || typeof value !== "object") return value;
    return new Proxy(value, {get(target,key) {
      const item = Reflect.get(target,key);
      return typeof item === "string" ? t(item) : item && typeof item === "object" ? localize(item) : item;
    }});
  }
  function apply() {
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
    document.querySelectorAll("[data-i18n]").forEach(element => {
      const source = element.dataset.i18n;
      element.textContent = t(source);
    });
    document.querySelectorAll("[data-i18n-attrs]").forEach(element => {
      const attributes = JSON.parse(element.dataset.i18nAttrs);
      for (const [attribute,source] of Object.entries(attributes)) element.setAttribute(attribute,t(source));
    });
    document.title = t("Dotii 管理中心");
  }
  function setLanguage(value) {
    if (!["en","zh-CN"].includes(value)) return;
    const changed = language !== value;
    language = value;
    try { localStorage.setItem("dotii-language",language); } catch {}
    apply();
    if (changed) window.dispatchEvent(new Event("dotii-language-changed"));
  }
  window.DotiiI18n = {ready,t,tr,localize,setLanguage,apply,get language(){return language;}};
  window.t = t;
  window.tr = tr;
  apply();
})();
