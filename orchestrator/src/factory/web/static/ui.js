const banner = document.getElementById("disconnected");
document.body.addEventListener("htmx:sseError", () => { banner.hidden = false; });
document.body.addEventListener("htmx:sseOpen", () => { banner.hidden = true; });
