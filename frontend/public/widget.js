/*
 * Beacon Pulse chat launcher. Add to any page (e.g. the Magento theme footer):
 *   <script src="https://pulse.beaconlighting.example/widget.js" defer></script>
 */
(function () {
  if (window.__beaconPulse) return;
  window.__beaconPulse = true;
  var origin = new URL(document.currentScript.src).origin;
  var open = false;
  var btn = document.createElement('button');
  btn.setAttribute('aria-label', 'Chat with Beacon Lighting');
  btn.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:2147483000;width:58px;height:58px;border-radius:50%;border:0;background:#000;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);display:grid;place-items:center';
  btn.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M13.5 2 4 14h6.5L9 22l11-13h-6.5z" fill="#FFC20E"/></svg>';
  var frame = document.createElement('iframe');
  frame.src = origin + '/widget';
  frame.title = 'Beacon Lighting support chat';
  frame.style.cssText = 'position:fixed;right:20px;bottom:90px;z-index:2147483000;width:min(380px,calc(100vw - 40px));height:min(600px,calc(100vh - 120px));border:0;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.25);display:none;background:#fff';
  btn.onclick = function () { open = !open; frame.style.display = open ? 'block' : 'none'; };
  document.body.appendChild(frame);
  document.body.appendChild(btn);
})();
