chrome.runtime.onInstalled.addListener(() => {
  const uiLang = (chrome.i18n && chrome.i18n.getUILanguage ? chrome.i18n.getUILanguage() : "en").toLowerCase();
  const isIT = uiLang.startsWith("it");
  chrome.contextMenus.create({
    id: "slsk-download",
    title: isIT ? "Scarica con Soulseek" : "Download with Soulseek",
    contexts: ["link", "selection"]
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== "slsk-download") return;
  const candidate = (info.linkUrl || info.selectionText || "").trim();
  if (!candidate) return;
  const target = "soulseekdl://" + encodeURIComponent(candidate);
  if (tab && tab.id) {
    chrome.tabs.update(tab.id, { url: target });
  } else {
    chrome.tabs.create({ url: target });
  }
});
