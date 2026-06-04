#!/usr/bin/env python3
"""
Scheduler render-dispatch smoke test (runs INSIDE a backend-image container).

Validates the multi-format render path the report scheduler now uses
(app.workers.report_scheduler._render_one) without depending on the
scheduler's clock/tick:

  * pdf  -> bytes starting with %PDF, ext "pdf"
  * html -> bytes containing <html, ext "html"
  * json -> valid JSON document, ext "json"
  * xml  -> bytes starting with <?xml, ext "xml"
  * csv  -> raises _UnsupportedFormat (no serializer yet)
  * pdf/html with an empty template -> raises ValueError (clear failure)

Run it where the app + renderers + DATABASE_URL exist (any backend container):

  docker cp scripts/scheduler_render_smoke.py svj_backend:/tmp/sched_smoke.py
  docker exec svj_backend python /tmp/sched_smoke.py

Exit code = number of failed checks (0 = green).
"""
from __future__ import annotations
import json
import sys

from app.services.report_render import build_live_context
from app.workers.report_scheduler import _render_one, _UnsupportedFormat

PASS = FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def bad(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    # Context with no DB hit (tag_ids=[] never touches the session), so this is
    # deterministic and needs no live tag data. A template with a tags loop
    # still renders fine against an empty list.
    ctx = build_live_context(None, [], "Asia/Kolkata")  # type: ignore[arg-type]
    ctx["report"]["name"] = "Scheduler Render Smoke"
    ctx["report"]["category"] = "on_demand"
    ctx["report"]["report_type"] = "current"

    dm = {
        "page_size": "A4",
        "orientation": "portrait",
        "template_html": (
            "<h1>{{ report.name }}</h1>"
            "<table>{% for t in tags_list %}<tr><td>{{ t.name }}</td>"
            "<td>{{ t.display }}</td></tr>{% endfor %}</table>"
        ),
    }
    dm_no_template = {"page_size": "A4", "orientation": "portrait", "template_html": ""}

    print("Scheduler render-dispatch smoke")

    # pdf
    try:
        b, ext = _render_one("pdf", dm, ctx)
        (ok if (ext == "pdf" and b[:4] == b"%PDF") else bad)(
            "render pdf", f"{len(b)} bytes, ext={ext}")
    except Exception as e:
        bad("render pdf", f"{type(e).__name__}: {e}")

    # html
    try:
        b, ext = _render_one("html", dm, ctx)
        (ok if (ext == "html" and b"<html" in b.lower()) else bad)(
            "render html", f"{len(b)} bytes, ext={ext}")
    except Exception as e:
        bad("render html", f"{type(e).__name__}: {e}")

    # json
    try:
        b, ext = _render_one("json", dm, ctx)
        doc = json.loads(b)
        (ok if (ext == "json" and "report" in doc) else bad)(
            "render json", f"{len(b)} bytes, ext={ext}")
    except Exception as e:
        bad("render json", f"{type(e).__name__}: {e}")

    # xml
    try:
        b, ext = _render_one("xml", dm, ctx)
        (ok if (ext == "xml" and b.lstrip().startswith(b"<?xml")) else bad)(
            "render xml", f"{len(b)} bytes, ext={ext}")
    except Exception as e:
        bad("render xml", f"{type(e).__name__}: {e}")

    # csv -> unsupported
    try:
        _render_one("csv", dm, ctx)
        bad("csv rejected as unsupported", "csv was accepted (should raise)")
    except _UnsupportedFormat:
        ok("csv rejected as unsupported")
    except Exception as e:
        bad("csv rejected as unsupported", f"wrong exception {type(e).__name__}: {e}")

    # pdf with no template -> ValueError
    try:
        _render_one("pdf", dm_no_template, ctx)
        bad("pdf without template fails clearly", "no error raised")
    except ValueError:
        ok("pdf without template fails clearly")
    except Exception as e:
        bad("pdf without template fails clearly", f"wrong exception {type(e).__name__}: {e}")

    print("-" * 50)
    print(f"PASS {PASS}   FAIL {FAIL}   " + ("GREEN" if FAIL == 0 else "FAILURE(S)"))
    return min(FAIL, 120)


if __name__ == "__main__":
    sys.exit(main())
