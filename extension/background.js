// Reporta la pestaña activa a la app local (http://127.0.0.1:8765). Nada sale de tu compu.
const ENDPOINT = "http://127.0.0.1:8765/tab";

async function report() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab) return;
    await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: tab.url || "", title: tab.title || "" }),
    });
  } catch (e) {
    // la app no está corriendo: no pasa nada
  }
}

chrome.tabs.onActivated.addListener(report);
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (tab.active && (info.url || info.title || info.status === "complete")) report();
});
chrome.windows.onFocusChanged.addListener(report);
chrome.runtime.onStartup.addListener(report);
chrome.runtime.onInstalled.addListener(report);

// Latido cada 30 s para que la app sepa que la extensión sigue viva
chrome.alarms.create("heartbeat", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(report);
