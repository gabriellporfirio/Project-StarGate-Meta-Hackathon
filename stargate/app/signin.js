/* Sign-in POST via fetch. fetch always sends an Origin header, which
   the server requires; some browsers omit Origin on a plain form POST,
   which the server must then refuse. Without JavaScript the plain form
   below still works in browsers that do send Origin on form POSTs. */
"use strict";

document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("signin-form");
  if (!form || typeof fetch !== "function") return;
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var msg = document.getElementById("signin-error");
    var input = document.getElementById("signin-token");
    var token = input ? input.value : "";
    fetch("/api/signin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token })
    }).then(function (res) {
      if (res.ok) {
        window.location.assign("/");
        return null;
      }
      return res.json().catch(function () { return {}; }).then(
        function (data) {
          var detail = (data && data.error) || ("sign in failed (" +
            res.status + ")");
          throw new Error(detail);
        });
    }).catch(function (err) {
      if (msg) msg.textContent = String((err && err.message) || err);
    });
  });
});
