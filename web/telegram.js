"use strict";
(() => {
  const app = window.Telegram && window.Telegram.WebApp;
  const status = document.getElementById("status");
  const message = document.getElementById("message");
  const library = document.getElementById("library");
  const feedback = document.getElementById("feedback");
  const initData = app && app.initData;
  if (!initData) {
    status.textContent = "Откройте эту страницу кнопкой «Лента» в личном чате с ботом. Для браузера используйте основной адрес сайта.";
    return;
  }
  app.ready();
  app.expand();
  let pending = false;
  async function request(path, values) {
    const response = await fetch(path, {
      method: "POST", credentials: "omit", cache: "no-store",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({init_data: initData, ...values})
    });
    if (!response.ok) {
      let detail = "Лента недоступна (HTTP " + response.status + "). Попробуйте закрыть и открыть её снова.";
      if ((response.headers.get("content-type") || "").includes("application/json")) {
        const body = await response.json();
        if (body.error) detail = body.error;
      }
      throw new Error(detail);
    }
    return response;
  }
  async function load(query) {
    if (pending) return;
    pending = true;
    feedback.textContent = "Загружаем материалы…";
    try {
      const response = await request("/telegram/feed", {query});
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      if (!document.getElementById("library-style")) {
        const style = document.createElement("style");
        style.id = "library-style";
        style.textContent = page.querySelector("style").textContent;
        document.head.appendChild(style);
      }
      library.innerHTML = page.body.innerHTML;
      message.hidden = true;
      feedback.textContent = "";
    } catch (error) {
      feedback.textContent = error.message;
      status.textContent = error.message;
    } finally { pending = false; }
  }
  library.addEventListener("submit", event => {
    if (!(event.target instanceof HTMLFormElement)) return;
    event.preventDefault();
    load(String(new FormData(event.target).get("q") || ""));
  });
  library.addEventListener("click", async event => {
    const link = event.target.closest("a[href]");
    if (!link) return;
    const url = new URL(link.href, window.location.origin);
    if (url.origin === window.location.origin && url.pathname === "/original") {
      event.preventDefault();
      try {
        feedback.textContent = "Готовим оригинал…";
        const id = Number(url.searchParams.get("id"));
        const response = await request("/telegram/original", {version_id: id});
        const blobUrl = URL.createObjectURL(await response.blob());
        const download = document.createElement("a");
        download.href = blobUrl;
        download.download = "source-" + id + ".bin";
        document.body.appendChild(download);
        download.click();
        download.remove();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
        feedback.textContent = "";
      } catch (error) { feedback.textContent = error.message; }
    } else if (["http:", "https:"].includes(url.protocol)) {
      event.preventDefault();
      app.openLink(url.href);
    }
  });
  load("");
})();
