/*
 * PD.FilterManager — generic per-table column filter state + URL sync.
 *
 * Hard dependencies:
 *   - PD.DataTable  (datatable.js)  — for applying filters to DataTables
 *   - PD.format     (format.js)     — for escapeRegex
 *
 * The pre-extraction code had three near-identical filter setups (contracts,
 * vendors, officers), each with their own activeFilters object, URL map, and
 * chip-renderer wiring. They drifted (officer URL map was duplicated and
 * inverted; flags column index was hardcoded as '6'). This wrapper takes a
 * column schema + URL spec and does all the bookkeeping in one place.
 *
 * Public API:
 *
 *   var fm = new PD.FilterManager({
 *     dataTable: PD.DataTable instance,
 *     columns: [
 *       { index, name, type, urlKey?, regex? }
 *     ],
 *     chipBar:    PD.ChipBar?,         optional — auto-renders chips on change
 *     hashFragment: 'tab-id'?,         hash to set on toURL()
 *     formatChipLabel: (col, f) => str,
 *     formatChipValue: (col, f) => str,
 *   });
 *
 *     index   — column index in the DataTable
 *     name    — display name (used as default chip label)
 *     type    — 'text' | 'range' | 'multiselect'
 *     urlKey  — URL param key. Omit to skip URL sync for this column. For
 *               type='range', _min and _max suffixes are auto-appended.
 *     regex   — multiselect only. When true, value strings are passed verbatim
 *               into the column search regex (so column cells like "flag1|flag2"
 *               can match against a single value); when false (default), values
 *               are escaped first.
 *
 *   fm.applyText(colIndex, value)
 *   fm.applyRange(colIndex, { min, max })       null bound = no constraint
 *   fm.applyMultiselect(colIndex, values)       replaces the value set
 *   fm.addToMultiselect(colIndex, value)
 *   fm.removeFromMultiselect(colIndex, value)   removes one; deletes filter when empty
 *   fm.clearFilter(colIndex)
 *   fm.clearAll()
 *   fm.fromURL(params?)                          accepts URLSearchParams or string
 *   fm.toURL()                                   history.replaceState
 *   fm.activeFilters                             exposed map: colIndex(string) -> filter
 *   fm.activeChips()                             [{label, value, onRemove}]
 *   fm.renderChips()                             writes to chipBar (no-op if null)
 *   fm.subscribe(fn)                             fn() fires after every mutation
 *
 * Filter object shapes:
 *   text:        { type:'text', value: string, name: string }
 *   range:       { type:'range', min: number|null, max: number|null, name: string }
 *   multiselect: { type:'multiselect', values: string[], name: string }
 */
(function (root) {
  var PD = root.PD = root.PD || {};

  function _checkDeps() {
    if (!PD.DataTable) throw new Error('PD.FilterManager: PD.DataTable is required');
    if (!PD.format || !PD.format.escapeRegex) throw new Error('PD.FilterManager: PD.format is required');
  }

  function FilterManager(opts) {
    _checkDeps();
    opts = opts || {};
    if (!opts.dataTable) throw new Error('PD.FilterManager: dataTable is required');
    if (!Array.isArray(opts.columns)) throw new Error('PD.FilterManager: columns array is required');

    this._dt = opts.dataTable;
    this._columns = opts.columns;
    this._byIndex = {};
    this._byUrlKey = {};
    var byIndex = this._byIndex, byUrlKey = this._byUrlKey;
    opts.columns.forEach(function (c) {
      byIndex[c.index] = c;
      if (c.urlKey) byUrlKey[c.urlKey] = c;
    });

    this._chipBar = opts.chipBar || null;
    this._hashFragment = opts.hashFragment || '';
    this._formatChipLabel = opts.formatChipLabel || null;
    this._formatChipValue = opts.formatChipValue || null;
    this._subscribers = [];
    this.activeFilters = {};   // colIndexStr -> filter object
    this._rangeRegistered = false;
  }

  FilterManager.prototype._col = function (colIndex) {
    var c = this._byIndex[colIndex];
    if (!c) throw new Error('PD.FilterManager: unknown column index ' + colIndex);
    return c;
  };

  FilterManager.prototype._notify = function () {
    var self = this;
    this._subscribers.forEach(function (s) { try { s(); } catch (e) { console.error(e); } });
    this.renderChips();
  };

  FilterManager.prototype.subscribe = function (fn) {
    this._subscribers.push(fn);
    return this;
  };

  // ── Apply / clear ──────────────────────────────────────────────────────────

  FilterManager.prototype.applyText = function (colIndex, value) {
    var col = this._col(colIndex);
    if (col.type !== 'text') throw new Error('column ' + colIndex + ' is not type=text');
    var key = String(colIndex);
    if (value === '' || value === null || value === undefined) {
      delete this.activeFilters[key];
      this._dt.api().column(col.index).search('');
    } else {
      this.activeFilters[key] = { type: 'text', value: String(value), name: col.name };
      this._dt.api().column(col.index).search(String(value));
    }
    this._dt.api().draw();
    this._notify();
  };

  FilterManager.prototype.applyRange = function (colIndex, spec) {
    var col = this._col(colIndex);
    if (col.type !== 'range') throw new Error('column ' + colIndex + ' is not type=range');
    var key = String(colIndex);
    spec = spec || {};
    var min = spec.min === '' || spec.min === undefined ? null : spec.min;
    var max = spec.max === '' || spec.max === undefined ? null : spec.max;
    if (min === null && max === null) {
      delete this.activeFilters[key];
    } else {
      this.activeFilters[key] = {
        type: 'range',
        min: min === null ? null : parseFloat(min),
        max: max === null ? null : parseFloat(max),
        name: col.name,
      };
    }
    this._ensureRangePredicate();
    this._dt.api().draw();
    this._notify();
  };

  FilterManager.prototype._ensureRangePredicate = function () {
    if (this._rangeRegistered) return;
    var self = this;
    // PD.DataTable.registerFilter is per-instance idempotent (its _filters
    // map is keyed by string and lives on the wrapper) AND auto-wraps the
    // predicate with an nTable gate. So one FM key works across instances
    // and we don't need an internal nTable check.
    this._dt.registerFilter('pd-fm-range', function (settings, data) {
      for (var k in self.activeFilters) {
        var f = self.activeFilters[k];
        if (f.type !== 'range') continue;
        var raw = data[parseInt(k)];
        if (raw === undefined) return false;
        var v = parseFloat(String(raw).replace(/[,$\s]/g, ''));
        if (isNaN(v)) return false;
        if (f.min !== null && v < f.min) return false;
        if (f.max !== null && v > f.max) return false;
      }
      return true;
    });
    this._rangeRegistered = true;
  };

  FilterManager.prototype.applyMultiselect = function (colIndex, values) {
    var col = this._col(colIndex);
    if (col.type !== 'multiselect') throw new Error('column ' + colIndex + ' is not type=multiselect');
    var key = String(colIndex);
    // Drop empty / whitespace-only values. Two real failure modes this guards
    // against: (a) a corrupt or stale URL like ?flags=, decodes via fromURL
    // into ['', ''] and would otherwise be stored verbatim and serialized
    // back into the URL as '?flags=,', poisoning shareable links forever;
    // (b) an upstream pipeline bug (e.g. FLAG_DEFS shipped without
    // filter_value) makes every dialog checkbox emit value="" — without
    // this guard the resulting filter renders empty chips and matches every
    // row via empty regex alternation '|'.
    var clean = (values || [])
      .map(function (v) { return v == null ? '' : String(v); })
      .filter(function (v) { return v.trim().length > 0; });
    if (!clean.length) {
      delete this.activeFilters[key];
      this._dt.api().column(col.index).search('');
    } else {
      this.activeFilters[key] = { type: 'multiselect', values: clean, name: col.name };
      var pattern = col.regex
        ? clean.join('|')
        : clean.map(PD.format.escapeRegex).join('|');
      this._dt.api().column(col.index).search(pattern, true, false, true);
    }
    this._dt.api().draw();
    this._notify();
  };

  FilterManager.prototype.addToMultiselect = function (colIndex, value) {
    var key = String(colIndex);
    var existing = this.activeFilters[key];
    var values = existing && existing.values ? existing.values.slice() : [];
    if (values.indexOf(value) < 0) values.push(value);
    this.applyMultiselect(colIndex, values);
  };

  FilterManager.prototype.removeFromMultiselect = function (colIndex, value) {
    var key = String(colIndex);
    var existing = this.activeFilters[key];
    if (!existing || !existing.values) return;
    var remaining = existing.values.filter(function (v) { return v !== value; });
    this.applyMultiselect(colIndex, remaining);
  };

  FilterManager.prototype.clearFilter = function (colIndex) {
    var col = this._col(colIndex);
    var key = String(colIndex);
    if (!this.activeFilters[key]) return;
    delete this.activeFilters[key];
    if (col.type !== 'range') {
      this._dt.api().column(col.index).search('');
    }
    this._dt.api().draw();
    this._notify();
  };

  FilterManager.prototype.clearAll = function () {
    var self = this;
    // Delete keys in place rather than reassigning. Consumers commonly alias
    // their own globals to fm.activeFilters (e.g. contractsFM.activeFilters =
    // window.activeFilters) so dialog code can keep mutating one shared
    // object. Reassigning to a new {} would silently break that alias —
    // dialog mutations after a clearAll would write to the old object and
    // toURL would read from the new (empty) one, producing empty share URLs.
    Object.keys(this.activeFilters).forEach(function (k) {
      var col = self._byIndex[parseInt(k)];
      if (col && col.type !== 'range') self._dt.api().column(col.index).search('');
      delete self.activeFilters[k];
    });
    this._dt.api().draw();
    this._notify();
  };

  // ── URL sync ───────────────────────────────────────────────────────────────

  FilterManager.prototype.toURL = function () {
    var params = new URLSearchParams();
    var self = this;
    Object.keys(this.activeFilters).forEach(function (k) {
      var f = self.activeFilters[k];
      var col = self._byIndex[parseInt(k)];
      if (!col || !col.urlKey) return;
      if (f.type === 'text') {
        params.set(col.urlKey, f.value);
      } else if (f.type === 'multiselect') {
        params.set(col.urlKey, f.values.join(','));
      } else if (f.type === 'range') {
        if (f.min !== null) params.set(col.urlKey + '_min', String(f.min));
        if (f.max !== null) params.set(col.urlKey + '_max', String(f.max));
      }
    });
    var qs = params.toString();
    var hash = this._hashFragment ? '#' + this._hashFragment : root.location.hash || '';
    var newURL = root.location.pathname + (qs ? '?' + qs : '') + hash;
    root.history.replaceState(null, '', newURL);
  };

  FilterManager.prototype.fromURL = function (params) {
    if (params === undefined) params = root.location.search;
    if (typeof params === 'string') params = new URLSearchParams(params);
    if (!params || !params.toString) return;
    var self = this;
    Object.keys(this._byUrlKey).forEach(function (urlKey) {
      var col = self._byUrlKey[urlKey];
      if (col.type === 'text') {
        var v = params.get(urlKey);
        if (v) self.applyText(col.index, v);
      } else if (col.type === 'multiselect') {
        var raw = params.get(urlKey);
        if (raw) self.applyMultiselect(col.index, raw.split(','));
      } else if (col.type === 'range') {
        var min = params.get(urlKey + '_min');
        var max = params.get(urlKey + '_max');
        if (min !== null || max !== null) self.applyRange(col.index, {min: min, max: max});
      }
    });
  };

  // ── Chips ──────────────────────────────────────────────────────────────────

  FilterManager.prototype.activeChips = function () {
    var self = this;
    var chips = [];
    Object.keys(this.activeFilters).forEach(function (k) {
      var f = self.activeFilters[k];
      var ci = parseInt(k);
      var col = self._byIndex[ci];
      if (!col) return;
      var label = self._formatChipLabel ? self._formatChipLabel(col, f) : col.name + ':';
      var value = self._formatChipValue ? self._formatChipValue(col, f) : self._defaultChipValue(f);
      chips.push({
        label: label,
        value: value,
        onRemove: (function (idx) {
          return function () { self.clearFilter(idx); };
        })(ci),
      });
    });
    return chips;
  };

  FilterManager.prototype._defaultChipValue = function (f) {
    if (f.type === 'text') return f.value;
    if (f.type === 'multiselect') return f.values.join(', ');
    if (f.type === 'range') {
      if (f.min !== null && f.max !== null) return f.min + '–' + f.max;
      if (f.min !== null) return '≥ ' + f.min;
      if (f.max !== null) return '≤ ' + f.max;
      return '';
    }
    return '';
  };

  FilterManager.prototype.renderChips = function () {
    if (!this._chipBar) return;
    this._chipBar.render(this.activeChips());
  };

  PD.FilterManager = FilterManager;
})(typeof window !== 'undefined' ? window : this);
