(function () {
  var KEY = "rc_consent";
  var stored = localStorage.getItem(KEY);

  function grantGA() {
    window["ga-disable-G-XXXXXXXXXX"] = false;
    gtag("consent", "update", {
      analytics_storage: "granted",
    });
  }

  function denyGA() {
    window["ga-disable-G-XXXXXXXXXX"] = true;
  }

  function hideBanner() {
    var el = document.getElementById("cookie-consent");
    if (el) el.remove();
  }

  if (stored === "granted") {
    grantGA();
    return;
  }
  if (stored === "denied") {
    denyGA();
    return;
  }

  // Default: deny until user acts
  denyGA();

  document.addEventListener("DOMContentLoaded", function () {
    var banner = document.createElement("div");
    banner.id = "cookie-consent";
    banner.innerHTML =
      '<div class="cc-inner">' +
      "<p>We use Google Analytics to understand how visitors use this site. No personal data is sold. " +
      '<a href="/privacy">Privacy policy</a>.</p>' +
      '<div class="cc-btns">' +
      '<button id="cc-accept">Accept</button>' +
      '<button id="cc-reject">Decline</button>' +
      "</div></div>";
    document.body.appendChild(banner);

    document.getElementById("cc-accept").addEventListener("click", function () {
      localStorage.setItem(KEY, "granted");
      grantGA();
      hideBanner();
    });
    document.getElementById("cc-reject").addEventListener("click", function () {
      localStorage.setItem(KEY, "denied");
      denyGA();
      hideBanner();
    });
  });
})();
