/*
 * PD.overlay — minimal full-screen overlay primitive.
 *
 * Plain DOM + CSS — no framework dependency, no focus-trap, no animation.
 * Used for the .filter-modal popovers in pull_usaspending/index.html
 * (small dropdowns shown over the chip bar, not document modals).
 *
 * The consumer is expected to provide the .filter-modal CSS — the overlay
 * element gets that class plus an optional caller-supplied className.
 *
 * Public API:
 *
 *   var ov = PD.overlay.open({
 *     content:   '<div>…</div>',  innerHTML for the overlay
 *     className: 'extra-cls',     optional, appended to .filter-modal
 *   });
 *   PD.overlay.close(ov);
 *
 * Behavior:
 *   - Click on the overlay backdrop (overlay element itself, not its
 *     children) closes it.
 *   - Escape key closes it (one-shot keydown listener, removed on close).
 *   - close() is idempotent; calling on an already-detached element is safe.
 */
(function (root) {
  var PD = root.PD = root.PD || {};

  function open(opts) {
    opts = opts || {};
    var doc = root.document;
    var overlay = doc.createElement('div');
    overlay.className = 'filter-modal' + (opts.className ? ' ' + opts.className : '');
    overlay.innerHTML = opts.content || '';
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) close(overlay);
    });
    var escHandler = function (e) {
      if (e.key === 'Escape') {
        close(overlay);
        doc.removeEventListener('keydown', escHandler);
      }
    };
    overlay._pdEscHandler = escHandler;
    doc.addEventListener('keydown', escHandler);
    doc.body.appendChild(overlay);
    return overlay;
  }

  function close(overlay) {
    if (!overlay) return;
    if (overlay._pdEscHandler) {
      root.document.removeEventListener('keydown', overlay._pdEscHandler);
      overlay._pdEscHandler = null;
    }
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
  }

  PD.overlay = { open: open, close: close };
})(typeof window !== 'undefined' ? window : this);
