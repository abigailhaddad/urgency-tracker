"""justifications.py — pull the actual written J&A text for urgency awards.

USAspending only records the urgency *reason code* (FAR 6.302-2), not the written
Justification & Approval. That narrative is published on SAM.gov as a "Justification"
notice, with the text living in a PDF attachment. This module retrieves it:

    award PIID → its FPDS solicitation_identifier (already in the feed)
      → Tango list_notices(solicitation_number=sol, notice_type='Justification')
      → SAM.gov resources API (public PDF attachments)
      → download + pypdf text extraction

Reality check: most urgency awards in the feed have NO retrievable J&A — they're
task/delivery orders, below-threshold sole sources, or otherwise never posted a J&A
to SAM.gov (confirmed against SAM.gov's own search, not just Tango). Only awards that
posted a standalone Justification notice (e.g. the Hoover Dam parking J&A) light up.
That's fine: the feature degrades gracefully — awards without one just omit the section.

Caching so a weekly CI run doesn't re-fetch the world:
  - scripts/ja_state.json  — compact {piid: {status, sol, notice_id, checked}} of every
    award we've checked. Committed. Negatives are cached, but a "none" result is
    re-checked while the award is still young (J&As can be posted weeks after award).
  - site/justifications/<piid>.json — full extracted text + metadata, ONLY for hits.
    Committed and served statically; the detail modal lazy-fetches it on row click, so
    urgent.json stays small.
"""
from __future__ import annotations

import base64
import datetime
import io
import json
import re
import time
import urllib.request

import pypdf

SAM = "https://sam.gov/api/prod/opps/v3/opportunities"
# Re-check a "none" award until it's this old — a J&A can be posted after the award
# shows up in FPDS, so a fresh "none" isn't necessarily permanent.
RECHECK_NONE_DAYS = 120
# Guard rails so one pathological attachment can't blow up the run / the repo.
MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_TEXT_CHARS = 400_000
# OCR fallback (scanned/image J&As have no text layer, so pypdf returns ""). Rendered
# pages go to a vision LLM via LiteLLM. Default to a current OpenAI model; override with
# OCR_MODEL. Cap pages so one huge scan can't run up an unbounded bill.
OCR_MODEL_DEFAULT = "gpt-5-mini"
# A real J&A is hundreds of characters. If pypdf yields less than OCR_RETRY_UNDER, the PDF
# is effectively image-only (a thin text layer like a stray date) — try OCR. If the best
# text is still under MIN_JA_CHARS, treat the record as 'empty' rather than badging junk.
OCR_RETRY_UNDER = 120
MIN_JA_CHARS = 80
OCR_MAX_PAGES = 15
OCR_RENDER_SCALE = 2.0  # ~144 dpi — enough for reliable transcription without huge images
# Tango allows 100 requests / 60s (burst) and 7,500 / day. Stay comfortably under the
# burst wall by pacing to ~90/min rather than sprinting into a 429 + a ~55s lockout.
TANGO_MIN_INTERVAL = 0.68
_JA_NAME_RE = re.compile(r"justif|\bj\W?&?\W?a\b|6[._-]?302|other.?than.?full", re.I)

_last_tango = [0.0]  # module-level clock for pacing (enrich runs single-threaded)


def _tango(fn, **kwargs):
    """Paced, rate-limit-aware wrapper around a Tango list method. Sleeps out any burst-
    limit 429 (Tango tells us how long) and paces calls so we don't hit it in the first
    place. `fn` is the bound method, e.g. client.list_notices / client.list_idvs."""
    from tango.exceptions import TangoRateLimitError
    for attempt in range(6):
        wait = TANGO_MIN_INTERVAL - (time.monotonic() - _last_tango[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = fn(**kwargs)
            _last_tango[0] = time.monotonic()
            return r
        except TangoRateLimitError as e:
            _last_tango[0] = time.monotonic()
            m = re.search(r"try again in (\d+)", str(e))
            wait = int(m.group(1)) + 2 if m else 15 * (attempt + 1)
            # A burst-limit reset is ≤ ~60s; anything much larger is the DAILY quota
            # (reset hours away). Don't hang the whole build for that — bail so this
            # award is recorded as 'error' and retried on the next run.
            if wait > 90:
                raise RuntimeError(f"Tango daily quota exhausted (reset in ~{wait}s)")
            time.sleep(wait)
    raise RuntimeError("Tango rate limit: exhausted retries")


def _tango_notices(c, **kwargs):
    return _tango(c.list_notices, **kwargs)


def _tango_idvs(c, **kwargs):
    return _tango(c.list_idvs, **kwargs)


def _sam_get(url, accept, binary=False, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"Accept": accept, "User-Agent": "urgency-tracker/1.0 (+github.com/abigailhaddad/urgency-tracker)"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read() if binary else json.loads(r.read())
        except Exception as e:  # noqa: BLE001 — best-effort; SAM has transient 5xx/timeouts
            last = e
    raise last


# Page furniture that PDF extraction interleaves with the body — drop it on reflow.
_NOISE_RE = re.compile(
    r"(?:\bPage\s+\d+\s+of\s+\d+\b"     # "…R0005  Page 1 of 6"
    r"|^WBR\b.*Template"                 # "WBR 1406.303-1 Template 1"
    r"|^eFile\b"                         # "eFile - B02"
    r"|^\(\d{2}/\d{4}\)\s*$"             # "(04/2021)"
    r"|^[_\-=]{5,}\s*$)",                # divider rules
    re.I)
_SEP_RE = re.compile(r"^\s*[—-]{4,}\s*$")   # our between-PDF em-dash separator


def _is_caps_head(s):
    """A short ALL-CAPS line — a document/section heading (e.g. 'SIGNATURES')."""
    return bool([c for c in s if c.isalpha()]) and s == s.upper() and len(s) <= 90 and len(s.split()) <= 12


def _reflow(text):
    """Turn per-visual-line PDF/OCR text into clean paragraphs: join wrapped lines,
    keep breaks between numbered sections and headings, drop page furniture, dehyphenate,
    and close the space-before-punctuation gaps left by redactions. Idempotent."""
    paras, buf, mode = [], [], None  # mode: 'head' | 'body' | None
    def flush():
        if buf:
            p = re.sub(r"\s{2,}", " ", " ".join(buf).strip())
            p = re.sub(r"\s+([.,;:)])", r"\1", p)   # "approximately ." -> "approximately."
            if p:
                paras.append(p)
            buf.clear()
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            # A blank line ends a paragraph ONLY if the text so far looks complete
            # (ends with sentence punctuation). Otherwise it's a page-break artifact
            # splitting a sentence mid-flow — keep accumulating across it.
            if buf and re.search(r"[.:;!?][\"')\]]?$", buf[-1]):
                flush(); mode = None
            continue
        if _NOISE_RE.search(s):
            continue
        if _SEP_RE.match(s):
            flush(); paras.append("———"); mode = None; continue
        if re.match(r"^\d+\.\s", s):            # numbered section — start a fresh paragraph
            flush(); buf.append(s); mode = "body"; continue
        if _is_caps_head(s):
            if mode != "head":
                flush()
            buf.append(s); mode = "head"; continue
        if mode == "head":                      # heading ended; body begins
            flush()
        mode = "body"
        if buf and buf[-1].endswith("-"):
            buf[-1] = buf[-1][:-1] + s          # dehyphenate a word split across lines
        else:
            buf.append(s)
    flush()
    # A paragraph that starts lowercase is almost always a continuation the page break
    # split off (prev line ended on an abbreviation/cite period) — fold it back.
    merged = []
    for p in paras:
        if merged and merged[-1] != "———" and p != "———" and p[:1].islower():
            merged[-1] = merged[-1].rstrip() + " " + p
        else:
            merged.append(p)
    return "\n\n".join(merged)[:MAX_TEXT_CHARS]


def _pdf_text(raw):
    try:
        pages = pypdf.PdfReader(io.BytesIO(raw)).pages
        t = "\n".join((p.extract_text() or "") for p in pages)
    except Exception:  # encrypted / malformed / scanned-image PDF → no text layer
        return ""
    return _reflow(t)


_OCR_PROMPT = (
    "This is one page of a federal Justification & Approval (J&A) document that was "
    "scanned as an image, so it has no selectable text. Transcribe ALL text on the page "
    "verbatim — headings, body, tables, signature blocks, form labels. Preserve reading "
    "order and line breaks. Do not summarize, add commentary, or describe the layout. "
    "Output only the transcribed text; if the page is blank, output nothing."
)


def _ocr_pdf(raw, api_key, model):
    """Transcribe a scanned/image PDF by rendering each page and sending it to a vision
    LLM (via LiteLLM). Returns the concatenated transcription, or "" on any failure —
    OCR is best-effort enrichment and must never break the build."""
    try:
        import fitz  # PyMuPDF — render PDF pages to PNG
        from litellm import completion
    except Exception:
        return ""
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception:
        return ""
    mat = fitz.Matrix(OCR_RENDER_SCALE, OCR_RENDER_SCALE)
    pages = []
    for page in list(doc)[:OCR_MAX_PAGES]:
        try:
            png = page.get_pixmap(matrix=mat).tobytes("png")
        except Exception:
            continue
        b64 = base64.b64encode(png).decode()
        try:
            resp = completion(
                model=model, api_key=api_key,  # no temperature: gpt-5 only allows the default
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": _OCR_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]}],
            )
            pages.append((resp.choices[0].message.content or "").strip())
        except Exception:
            # A single page failing (rate limit, transient error) shouldn't lose the rest.
            continue
    return _reflow("\n\n".join(p for p in pages if p))


def _sam_attachments(notice_id_nodash):
    """Public PDF attachments for a SAM.gov opportunity, via its resources endpoint."""
    data = _sam_get(f"{SAM}/{notice_id_nodash}/resources", "application/hal+json")
    out = []
    for grp in data.get("_embedded", {}).get("opportunityAttachmentList", []):
        for a in grp.get("attachments", []):
            name = a.get("name") or ""
            is_pdf = str(a.get("mimeType", "")).lower().endswith("pdf") or name.lower().endswith(".pdf")
            if is_pdf and a.get("accessStatus") == "public" and a.get("resourceId") and int(a.get("size") or 0) <= MAX_PDF_BYTES:
                out.append({"name": name, "resourceId": a["resourceId"], "size": a.get("size")})
    return out


def _justification_notices(c, keys):
    """Justification-type notices keyed on the FPDS solicitation number OR the award
    PIID — agencies file the J&A under either. Two paced Tango calls at most (one per
    distinct key). Returns a de-duplicated list of notice dicts."""
    notices = {}
    for key in keys:
        for n in _tango_notices(c, solicitation_number=key, notice_type="Justification", limit=25).results:
            n = dict(n)
            notices[n["notice_id"]] = n
    return list(notices.values())


def _extract_notice(nid, ocr=None):
    """Download + extract every public PDF on one SAM.gov notice. When a PDF has no text
    layer (scanned image) and an `ocr` callable is supplied, fall back to OCR on it.
    Returns (pieces, pdf_meta, nid_nodash)."""
    nid_nodash = nid.replace("-", "")
    pieces, pdfs = [], []
    for a in _sam_attachments(nid_nodash):
        raw = _sam_get(f"{SAM}/resources/files/{a['resourceId']}/download", "application/octet-stream", binary=True)
        txt, via = _pdf_text(raw), "text"
        # Too little text usually means an image-only PDF with a stray text fragment — OCR it
        # and keep whichever is longer.
        if len(txt.strip()) < OCR_RETRY_UNDER and ocr is not None:
            otxt = ocr(raw)
            if len(otxt.strip()) > len(txt.strip()):
                txt, via = otxt, "ocr"
        pdfs.append({"name": a["name"], "chars": len(txt), "via": via if txt else "none"})
        if txt:
            pieces.append(txt)
    return pieces, pdfs, nid_nodash


def fetch_justification(c, piid, sol, ocr=None):
    """Return a justification record for one award, or a {'status': 'none'} record.

    Authoritative source: SAM.gov "Justification" notices, matched on the FPDS
    solicitation number OR the award PIID — agencies key the notice on either, and in
    practice the PIID matches more often than the solicitation number does. Within a
    hit, prefer J&A-named PDFs; if none are named that way, take them all. Scanned PDFs
    with no text layer are recovered via the optional `ocr` callable.

    Most urgency awards return 'none' — they never posted a J&A to SAM.gov (task/delivery
    orders, below-threshold sole sources); that's expected, not a miss. Raises on hard
    errors so the caller can mark 'error' and retry next run.
    """
    keys = [k for k in dict.fromkeys([str(sol or "").strip(), str(piid or "").strip()])
            if k and k.lower() != "none"]
    if not keys:
        return {"piid": piid, "status": "none", "sol": sol}
    ja_notices = _justification_notices(c, keys)
    if not ja_notices:
        return {"piid": piid, "status": "none", "sol": sol}

    pieces, pdfs, nid_used = [], [], None
    for n in ja_notices:
        p, meta, nid_nodash = _extract_notice(n["notice_id"], ocr=ocr)
        named = [i for i, m in enumerate(meta) if _JA_NAME_RE.search(m["name"])]
        keep = named if named else list(range(len(meta)))
        pdfs += [meta[i] for i in keep]
        pieces += [p[i] for i in keep if i < len(p)]
        nid_used = nid_used or nid_nodash

    sam_url = f"https://sam.gov/opp/{nid_used}/view"
    text = ("\n\n" + "—" * 8 + "\n\n").join(pieces)  # em-dash rule between PDFs
    ocred = any(m.get("via") == "ocr" for m in pdfs)
    if len(text.strip()) < MIN_JA_CHARS:
        # A J&A exists but yielded no usable text even after OCR (image-only/restricted).
        # Record the pointer so the modal can still link out to SAM.gov.
        return {"piid": piid, "status": "empty", "sol": sol, "notice_id": nid_used,
                "sam_url": sam_url, "pdfs": pdfs}
    return {"piid": piid, "status": "ok", "sol": sol, "notice_id": nid_used,
            "sam_url": sam_url, "pdfs": pdfs, "text": text, "ocr": ocred}


def _idv_solicitation(c, parent_piid):
    """Resolve a parent IDV's own solicitation number via Tango (its J&A notice is keyed
    on that, not on the IDV PIID). Two paced calls; returns None if unresolvable."""
    try:
        r = _tango_idvs(c, piid=parent_piid, limit=1)
        if not r.results:
            return None
        key = dict(r.results[0]).get("key")
        if not key:
            return None
        raw = c._get(f"/api/idvs/{key}/", {})
        return raw.get("solicitation_identifier")
    except Exception:  # noqa: BLE001
        return None


def fetch_parent_justification(c, parent_piid, ocr=None):
    """A delivery order's own urgency rarely has a posted J&A — but the PARENT vehicle it
    was placed against might. Look for a Justification notice on the parent IDV (matched by
    the IDV's solicitation number OR its PIID) and pull it, tagged source='parent'. This
    justifies the *vehicle*, not the specific order — the UI must label it as such."""
    keys = [k for k in [str(parent_piid or "").strip()] if k and k.lower() != "none"]
    if not keys:
        return {"status": "none"}
    sol = _idv_solicitation(c, parent_piid)
    if sol and str(sol).strip().lower() != "none":
        keys.append(str(sol).strip())
    keys = list(dict.fromkeys(keys))
    ja_notices = _justification_notices(c, keys)
    if not ja_notices:
        return {"status": "none"}
    pieces, pdfs, nid_used = [], [], None
    for n in ja_notices:
        p, meta, nid_nodash = _extract_notice(n["notice_id"], ocr=ocr)
        named = [i for i, m in enumerate(meta) if _JA_NAME_RE.search(m["name"])]
        keep = named if named else list(range(len(meta)))
        pdfs += [meta[i] for i in keep]
        pieces += [p[i] for i in keep if i < len(p)]
        nid_used = nid_used or nid_nodash
    if nid_used is None:
        return {"status": "none"}
    sam_url = f"https://sam.gov/opp/{nid_used}/view"
    text = ("\n\n" + "—" * 8 + "\n\n").join(pieces)
    ok = len(text.strip()) >= MIN_JA_CHARS
    rec = {"status": "ok" if ok else "empty", "source": "parent",
           "parent_piid": parent_piid, "notice_id": nid_used, "sam_url": sam_url, "pdfs": pdfs}
    if ok:
        rec["text"] = text
        rec["ocr"] = any(m.get("via") == "ocr" for m in pdfs)
    return rec


def _should_fetch(piid, award, state, today, ocr_enabled=False, recheck_none=True):
    st = state.get(piid)
    if st is None:
        return True  # never checked — always fetch
    if st.get("status") == "ok":
        return False  # already have the text — J&As don't change once posted
    if st.get("status") == "empty":
        # Scanned J&A we couldn't read before. Re-run only if OCR is now available
        # (otherwise we'd just get the same empty result and burn an API call).
        return ocr_enabled
    if st.get("status") == "error":
        return True  # transient failure last time — always retry
    # status == 'none'. Re-check while the award is young (a J&A can post weeks after
    # the award lands in FPDS) — unless the caller opted out (e.g. a one-time full seed
    # that just wants every award checked once, without re-querying known 'none's).
    if not recheck_none:
        return False
    d = str(award.get("date") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return False
    age = (today - datetime.date.fromisoformat(d)).days
    return age <= RECHECK_NONE_DAYS


def enrich(awards, sol_by_piid, ja_dir, state_path, api_key,
           today=None, max_checks=1500, log=print,
           ocr_api_key=None, ocr_model=OCR_MODEL_DEFAULT, recheck_none=True,
           parent_by_piid=None):
    """Populate `award['ja']` (bool) for every award, fetching any newly-needed J&As.

    Runs single-threaded and paced: Tango's 100-req/min burst cap makes concurrency
    pointless (all workers would share the same budget) and a sprint just triggers a
    ~55s lockout. At most `max_checks` awards are (re)checked per run — the newest first,
    since a fresh award is the one most likely to have just posted its J&A — so no single
    run can blow the daily quota; the rest roll over to the next run.

    Writes per-hit files under `ja_dir` and updates the checked-state cache at
    `state_path`. Best-effort: individual fetch failures are logged and treated as
    'error' (retried next run), never fatal.
    """
    from tango import TangoClient
    today = today or datetime.date.today()
    ja_dir.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    # OCR fallback for scanned J&As, only if a vision-LLM key was provided.
    ocr = (lambda raw: _ocr_pdf(raw, ocr_api_key, ocr_model)) if ocr_api_key else None
    if ocr:
        log(f"  justifications: OCR fallback enabled ({ocr_model}) for scanned J&As.")

    todo = [a for a in awards
            if _should_fetch(a["piid"], a, state, today, ocr_enabled=bool(ocr), recheck_none=recheck_none)]
    todo.sort(key=lambda a: str(a.get("date") or ""), reverse=True)  # newest first
    capped = len(todo) > max_checks
    if capped:
        log(f"  justifications: {len(todo)} to (re)check exceeds cap {max_checks}; "
            f"doing the {max_checks} newest, rest roll to next run.")
        todo = todo[:max_checks]
    log(f"  justifications: {len(awards)} awards, {len(todo)} to (re)check this run "
        f"({len(awards) - len(todo)} cached/skipped).")

    parent_by_piid = parent_by_piid or {}
    parent_cache = {}   # parent_piid -> record (dedupe shared vehicles)
    c = TangoClient(api_key=api_key)
    hits = errs = 0
    for a in todo:
        piid, sol = a["piid"], sol_by_piid.get(a["piid"], "")
        try:
            rec = fetch_justification(c, piid, sol, ocr=ocr)
            # No J&A of its own? For an order riding a vehicle, try the PARENT's J&A.
            if rec["status"] == "none":
                pp = str(parent_by_piid.get(piid, "") or "").strip()
                if pp and pp.lower() != "none":
                    if pp not in parent_cache:
                        parent_cache[pp] = fetch_parent_justification(c, pp, ocr=ocr)
                    prec = parent_cache[pp]
                    if prec.get("status") in ("ok", "empty"):
                        rec = dict(prec, sol=sol)   # source='parent' carried through
        except Exception as e:  # noqa: BLE001
            rec = {"piid": piid, "status": "error", "sol": sol, "error": str(e)[:200]}
        rec["piid"] = piid
        # No run timestamps in the committed artifacts — keep them deterministic so an
        # unchanged week produces byte-identical files (no needless commit/redeploy).
        if rec["status"] in ("ok", "empty"):
            # Full record (with text) goes to the served per-award file.
            (ja_dir / f"{piid}.json").write_text(json.dumps(rec, indent=1))
            if rec["status"] == "ok":
                hits += 1
        elif rec["status"] == "error":
            errs += 1
        # Keep the state cache compact: never store the big `text` blob in it.
        state[piid] = {k: v for k, v in rec.items() if k != "text"}

    state_path.write_text(json.dumps(state, indent=1, sort_keys=True))

    # `ja` flag drives whether the modal even tries to fetch the per-award file.
    have = {p for p, st in state.items() if st.get("status") in ("ok", "empty")}
    for a in awards:
        a["ja"] = a["piid"] in have
    n = sum(1 for a in awards if a["ja"])
    log(f"  justifications: {n} awards have a J&A on file "
        f"(+{hits} new this run, {errs} errors to retry next run).")
    return n
