/*
 * PD.ChipBar — render a row of removable filter chips.
 * No external dependencies (vanilla DOM).
 *
 * Public API:
 *   var bar = new PD.ChipBar({ barEl, emptyEl });
 *   bar.render([
 *     { label: 'Agency:', value: 'DOD',          onRemove: function(){...} },
 *     { label: 'Flags:',  value: 'UCA',          onRemove: function(){...} },
 *   ]);
 *   bar.clear();
 *
 *   barEl   — container element (or selector) to populate with chips
 *   emptyEl — element (or selector) shown only when there are zero chips
 *
 * Each call to render() replaces the chips currently in the bar — it does not
 * append. emptyEl visibility is toggled to match.
 *
 * Chip DOM structure (CSS classes are stable; consumers style them):
 *   <div class="filter-chip column-filter-chip">
 *     <span class="filter-chip-label">{label}</span>
 *     <span class="filter-chip-value">{value}</span>
 *     <span class="filter-chip-remove">×</span>
 *   </div>
 */
(function (root) {
  var PD = root.PD = root.PD || {};

  function _resolve(elOrSelector) {
    if (!elOrSelector) return null;
    if (typeof elOrSelector === 'string') return document.querySelector(elOrSelector);
    return elOrSelector;
  }

  function ChipBar(opts) {
    opts = opts || {};
    this._barEl = _resolve(opts.barEl);
    this._emptyEl = _resolve(opts.emptyEl);
    if (!this._barEl) {
      throw new Error('PD.ChipBar: barEl is required and must resolve to an element');
    }
  }

  ChipBar.prototype.render = function (chips) {
    chips = chips || [];
    // Remove only the chips we manage — leaves any sibling content (labels,
    // copy-URL buttons, etc.) inside the bar untouched.
    this._barEl.querySelectorAll('.filter-chip.column-filter-chip').forEach(function (c) {
      c.remove();
    });
    if (this._emptyEl) {
      this._emptyEl.style.display = chips.length ? 'none' : '';
    }
    var bar = this._barEl;
    chips.forEach(function (chip) {
      var el = document.createElement('div');
      el.className = 'filter-chip column-filter-chip';

      var labelSpan = document.createElement('span');
      labelSpan.className = 'filter-chip-label';
      labelSpan.textContent = chip.label;

      var valueSpan = document.createElement('span');
      valueSpan.className = 'filter-chip-value';
      valueSpan.textContent = chip.value;

      var removeSpan = document.createElement('span');
      removeSpan.className = 'filter-chip-remove';
      removeSpan.textContent = '×';
      if (chip.onRemove) removeSpan.addEventListener('click', chip.onRemove);

      el.appendChild(labelSpan);
      el.appendChild(valueSpan);
      el.appendChild(removeSpan);
      bar.appendChild(el);
    });
  };

  ChipBar.prototype.clear = function () { this.render([]); };

  PD.ChipBar = ChipBar;
})(typeof window !== 'undefined' ? window : this);
