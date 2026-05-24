function shareOnX(indexKey, text) {
  var url = encodeURIComponent("https://www.regimecompass.com/share/" + indexKey);
  var t = encodeURIComponent(text || "Market regime update from Regime Compass");
  window.open("https://x.com/intent/tweet?url=" + url + "&text=" + t, "_blank", "width=600,height=400");
}

function downloadCard(indexKey) {
  var a = document.createElement("a");
  a.href = "/api/card/" + indexKey;
  a.download = indexKey + "-regime-card.png";
  a.click();
}

function copyShareLink(indexKey) {
  var url = "https://www.regimecompass.com/share/" + indexKey;
  navigator.clipboard.writeText(url).then(function () {
    var btn = document.querySelector('[data-copy="' + indexKey + '"]');
    if (btn) {
      var orig = btn.innerHTML;
      btn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="var(--bull)" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>';
      setTimeout(function () { btn.innerHTML = orig; }, 1500);
    }
  });
}

function renderShareButtons(containerId, indexKey, indexName) {
  var el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML =
    '<button onclick="shareOnX(\'' + indexKey + '\', \'' + (indexName || "").replace(/'/g, "") + ' regime update\')" class="share-btn" title="Post on X">' +
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg></button>' +
    '<button onclick="downloadCard(\'' + indexKey + '\')" class="share-btn" title="Download card">' +
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3v13m0 0l-4.5-4.5M12 16l4.5-4.5M5 20h14"/></svg></button>' +
    '<button onclick="copyShareLink(\'' + indexKey + '\')" class="share-btn" data-copy="' + indexKey + '" title="Copy link">' +
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg></button>';
}
