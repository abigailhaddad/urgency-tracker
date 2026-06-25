/*
 * PD.format — formatting and string-safety helpers.
 * No external dependencies.
 *
 * Public API:
 *   PD.format.currency(n)         → "$1,234,567" (Intl.NumberFormat, 0 decimals)
 *   PD.format.compactCurrency(n)  → "$1.5M" / "$1.2B" / "$45K" (legacy fmtM)
 *   PD.format.date(s)             → "Mar 5, 2026" given "2026-03-05"
 *   PD.format.escapeHtml(s)       → HTML-attribute-safe string
 *   PD.format.escapeRegex(s)      → regex-literal-safe string
 */
(function (root) {
  var PD = root.PD = root.PD || {};
  var DASH = '—';  // em-dash, used as the "no value" placeholder

  var _currencyFmt = (typeof Intl !== 'undefined' && Intl.NumberFormat)
    ? new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 0})
    : null;

  function currency(n) {
    if (n === null || n === undefined || n === '') return DASH;
    var num = parseFloat(n);
    if (isNaN(num)) return DASH;
    if (_currencyFmt) return _currencyFmt.format(Math.round(num));
    // Fallback for environments without Intl: best-effort thousands grouping.
    return '$' + Math.round(num).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  // Compact ("$1.5M") representation for tight UI surfaces — chart labels,
  // stat cards, table cells. Mirrors the pre-extraction fmtM behavior so the
  // visual change is zero.
  function compactCurrency(n) {
    if (!n && n !== 0) return DASH;
    var num = parseFloat(n);
    if (isNaN(num)) return DASH;
    var a = Math.abs(num);
    if (a >= 1e12)  return '$' + (num / 1e12).toFixed(1) + 'T';
    if (a >= 1e9)   return '$' + (num / 1e9).toFixed(1) + 'B';
    if (a >= 100e6) return '$' + Math.round(num / 1e6) + 'M';
    if (a >= 1e6)   return '$' + (num / 1e6).toFixed(1) + 'M';
    if (a >= 1e3)   return '$' + Math.round(num / 1e3) + 'K';
    return '$' + Math.round(num);
  }

  // Render an ISO-like date (YYYY-MM-DD) as "Mon D, YYYY". Returns the input
  // unchanged for inputs that don't parse — caller controls fallback string.
  function date(s) {
    if (!s) return '';
    var d = new Date(String(s) + (String(s).length === 10 ? 'T00:00:00Z' : ''));
    if (isNaN(d.getTime())) return String(s);
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return months[d.getUTCMonth()] + ' ' + d.getUTCDate() + ', ' + d.getUTCFullYear();
  }

  var _htmlEscapeMap = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'};
  function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, function (m) { return _htmlEscapeMap[m]; });
  }

  function escapeRegex(s) {
    return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  PD.format = {
    currency: currency,
    compactCurrency: compactCurrency,
    date: date,
    escapeHtml: escapeHtml,
    escapeRegex: escapeRegex,
  };
})(typeof window !== 'undefined' ? window : this);
