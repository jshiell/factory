const banner = document.getElementById("disconnected");
document.body.addEventListener("htmx:sseError", () => { banner.hidden = false; });
document.body.addEventListener("htmx:sseOpen", () => { banner.hidden = true; });

// A stream runs only while the tab is visible: every open tab would otherwise
// hold one SSE connection, and the browser's per-host cap stalls new pages.
// The sse extension opens every source (retries included) through
// htmx.createEventSource, so wrapping it sees each one before it connects.
const openSource = htmx.createEventSource
  || ((url) => new EventSource(url, { withCredentials: true }));
let live = null;
let paused = false;

function pause() {
  // close() fires no error event, so neither the banner nor a retry follows.
  if (live) live.close();
  live = null;
  paused = true;
}

htmx.createEventSource = (url) => {
  if (live) live.close();
  const source = openSource(url);
  live = source;
  if (document.hidden) pause();
  return source;
};

// htmx.process skips an element whose attributes hash as before, so a new
// epoch makes it drop its internal data and let the extension connect again.
// That path fires no htmx:beforeCleanupElement, hence pause() closing the old
// source itself.
function resume() {
  if (!paused) return;
  paused = false;
  const stream = document.querySelector("[sse-connect]");
  if (!stream) return;
  const epoch = Number(stream.getAttribute("data-sse-epoch") || 0) + 1;
  stream.setAttribute("data-sse-epoch", String(epoch));
  htmx.process(stream);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) pause(); else resume();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) resume();
});
