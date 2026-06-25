/*
 * PD.loadDataset — fetch a manifest of JSON files in parallel.
 * No external dependencies (uses native fetch).
 *
 * Public API:
 *   PD.loadDataset(manifest)
 *
 *     manifest: { [key: string]: string | {url: string, optional?: boolean, fallback?: any} }
 *
 *   String entry → fetch the URL and reject the whole bundle on any failure
 *   (same as before).
 *   Object entry with `optional: true` → swallow HTTP 404 and resolve that
 *   key to `fallback` (defaults to `[]`). Other failures (5xx, network,
 *   malformed JSON) still reject. Useful for datasets that are produced by
 *   a later pipeline step than the one populating origin/prod — without
 *   this, a single missing file rejects Promise.all and the whole page
 *   hangs in its loading state (see run 26113335411).
 *
 *   Returns Promise<{ [key: string]: any }> resolving to the parsed JSON
 *   for each manifest key. Rejects with a labeled Error naming the offending
 *   URL on the first non-tolerated failure.
 */
(function (root) {
  var PD = root.PD = root.PD || {};

  function _fetchJson(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) {
        var err = new Error(url + ' → HTTP ' + r.status);
        err.status = r.status;
        throw err;
      }
      return r.json().catch(function (e) {
        throw new Error(url + ' → invalid JSON (' + e.message + ')');
      });
    });
  }

  function _loadEntry(entry) {
    if (typeof entry === 'string') {
      return _fetchJson(entry);
    }
    if (entry && typeof entry === 'object' && typeof entry.url === 'string') {
      var fallback = ('fallback' in entry) ? entry.fallback : [];
      var p = _fetchJson(entry.url);
      if (entry.optional) {
        return p.catch(function (err) {
          if (err && err.status === 404) return fallback;
          throw err;
        });
      }
      return p;
    }
    return Promise.reject(new Error('loadDataset: manifest entry must be a URL string or {url, optional?, fallback?}'));
  }

  function loadDataset(manifest) {
    if (!manifest || typeof manifest !== 'object') {
      return Promise.reject(new Error('loadDataset: manifest must be an object of {key: url}'));
    }
    var keys = Object.keys(manifest);
    var promises = keys.map(function (k) { return _loadEntry(manifest[k]); });
    return Promise.all(promises).then(function (values) {
      var out = {};
      keys.forEach(function (k, i) { out[k] = values[i]; });
      return out;
    });
  }

  PD.loadDataset = loadDataset;
})(typeof window !== 'undefined' ? window : this);
