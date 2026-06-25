/*
 * PD.DataTable — DataTables wrapper that owns its ext.search lifecycle.
 *
 * Hard dependencies: jQuery, DataTables (window.$ with .DataTable plugin).
 *
 * Public API:
 *   var dt = new PD.DataTable(selector, dtOptions);
 *
 *     selector — CSS selector or element passed to $().DataTable
 *     dtOptions — DataTables options object (passed through verbatim)
 *
 *   dt.api()                         underlying $.fn.dataTable.Api instance
 *   dt.registerFilter(key, predicate) idempotent ext.search registration; the
 *                                     predicate is wrapped so it only runs when
 *                                     this DataTable is the one being searched
 *   dt.unregisterFilter(key)         remove a previously-registered predicate
 *   dt.destroy()                     destroy the table AND deregister its filters
 *
 * Why this exists:
 *   The pre-extraction code pushed onto the global $.fn.dataTable.ext.search
 *   array directly, with manual `if (settings.nTable !== this.table().node())`
 *   guards in every closure. Re-init left ghost predicates that referenced a
 *   destroyed table. This wrapper:
 *
 *     - Tracks each registered predicate by (instance, key) so re-registration
 *       replaces rather than stacks.
 *     - Auto-injects the table-identity guard so callers don't write it.
 *     - Removes all of an instance's predicates on destroy().
 */
(function (root) {
  var PD = root.PD = root.PD || {};

  function _$() {
    var $ = root.jQuery || root.$;
    if (!$ || !$.fn || !$.fn.dataTable) {
      throw new Error('PD.DataTable: jQuery + DataTables are required');
    }
    return $;
  }

  function DataTable(selector, options) {
    var $ = _$();
    this._$ = $;
    this._api = $(selector).DataTable(options || {});
    this._filters = {};  // key -> wrapped predicate function
  }

  DataTable.prototype.api = function () { return this._api; };

  DataTable.prototype.registerFilter = function (key, predicate) {
    var $ = this._$;
    var self = this;
    var existing = this._filters[key];
    if (existing) {
      var idx = $.fn.dataTable.ext.search.indexOf(existing);
      if (idx >= 0) $.fn.dataTable.ext.search.splice(idx, 1);
    }
    var wrapped = function (settings, data, dataIndex) {
      // Only apply this predicate to this wrapper's underlying table — without
      // this guard a predicate registered for one table would filter every
      // other DataTable on the page.
      if (settings.nTable !== self._api.table().node()) return true;
      return predicate(settings, data, dataIndex);
    };
    this._filters[key] = wrapped;
    $.fn.dataTable.ext.search.push(wrapped);
  };

  DataTable.prototype.unregisterFilter = function (key) {
    var $ = this._$;
    var fn = this._filters[key];
    if (!fn) return;
    var idx = $.fn.dataTable.ext.search.indexOf(fn);
    if (idx >= 0) $.fn.dataTable.ext.search.splice(idx, 1);
    delete this._filters[key];
  };

  DataTable.prototype.destroy = function () {
    var $ = this._$;
    Object.keys(this._filters).forEach(function (k) {
      var fn = this._filters[k];
      var idx = $.fn.dataTable.ext.search.indexOf(fn);
      if (idx >= 0) $.fn.dataTable.ext.search.splice(idx, 1);
    }, this);
    this._filters = {};
    // No api.off() — DataTables 1.13's destroy() detaches its own listeners,
    // and a bare .off() throws ("Cannot read properties of undefined") on
    // empty event names.
    this._api.destroy();
    this._api = null;
  };

  PD.DataTable = DataTable;
})(typeof window !== 'undefined' ? window : this);
