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

document.addEventListener("visibilitychange", () => {
  if (document.hidden) pause();
});
