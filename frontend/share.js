function shareOnX(indexKey, text) {
  var url = encodeURIComponent("https://web-production-05f4d0.up.railway.app/share/" + indexKey);
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
  var url = "https://web-production-05f4d0.up.railway.app/share/" + indexKey;
  navigator.clipboard.writeText(url).then(function () {
    var btn = document.querySelector('[data-copy="' + indexKey + '"]');
    if (btn) {
      btn.textContent = "Copied!";
      setTimeout(function () { btn.textContent = "Link"; }, 1500);
    }
  });
}

function renderShareButtons(containerId, indexKey, indexName) {
  var el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML =
    '<div class="share-bar">' +
    '<button onclick="shareOnX(\'' + indexKey + '\', \'' + (indexName || "").replace(/'/g, "") + ' regime on Regime Compass\')" class="share-btn share-x" title="Share on X">' +
    '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg></button>' +
    '<button onclick="downloadCard(\'' + indexKey + '\')" class="share-btn share-dl" title="Download card image">' +
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg></button>' +
    '<button onclick="copyShareLink(\'' + indexKey + '\')" class="share-btn share-cp" data-copy="' + indexKey + '" title="Copy share link">Link</button>' +
    '</div>';
}
