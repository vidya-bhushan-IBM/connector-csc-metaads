#!/usr/bin/env python3
"""
Meta Ads API Endpoint Validation Script
connector-csc-metaads

Usage:
  python validate_endpoints.py \
    --token YOUR_ACCESS_TOKEN \
    --account act_3949033192058517 \
    --page 1336499462872322 \
    --campaign 120250369558760382 \
    --adset 120250369565280382 \
    --creative 1079984694640433

Output:
  tests/reports/validation_results.json
  tests/reports/validation_report.html
"""

import argparse
import json
import os
import re
import time
import copy
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Run: pip install requests")
    exit(1)

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_URL   = "https://graph.facebook.com/v26.0"
REPORT_DIR = os.path.join(os.path.dirname(__file__), "reports")
ENDPOINTS  = os.path.join(os.path.dirname(__file__), "endpoints.json")

# ── Token context (mutable during run) ────────────────────────────────────────
context = {}


def resolve(value, ctx):
    """Replace {placeholder} tokens in strings using context dict."""
    if not isinstance(value, str):
        return value
    def replace(m):
        key = m.group(1)
        return str(ctx.get(key, m.group(0)))
    return re.sub(r'\{(\w+)\}', replace, value)


def resolve_dict(d, ctx):
    """Recursively resolve placeholders in a dict or list."""
    if isinstance(d, dict):
        return {k: resolve_dict(v, ctx) for k, v in d.items()}
    if isinstance(d, list):
        return [resolve_dict(i, ctx) for i in d]
    return resolve(d, ctx)


def run_endpoint(ep, token, ctx, rate_delay=0.3):
    """Execute a single endpoint test and return a result dict."""
    result = {
        "id":         ep["id"],
        "group":      ep["group"],
        "name":       ep["name"],
        "method":     ep["method"],
        "status":     None,
        "http_code":  None,
        "error_code": None,
        "error_msg":  None,
        "response":   None,
        "note":       ep.get("note", ""),
        "url":        None,
    }

    dep = ep.get("depends_on")
    if dep and ctx.get(f"failed_{dep}"):
        result["status"] = "SKIP"
        result["note"]   = f"Skipped — dependency {dep} failed"
        return result

    path = resolve(ep["path"], ctx)
    if re.search(r'\{[^}]+\}', path):
        result["status"] = "SKIP"
        result["note"]   = f"Skipped — missing context value in path: {path}"
        return result

    url = f"{BASE_URL}/{path}"
    result["url"] = url

    params = resolve_dict(copy.deepcopy(ep.get("params", {})), ctx)
    body   = resolve_dict(copy.deepcopy(ep.get("body",   {})), ctx)

    try:
        method = ep["method"].upper()
        if method == "GET":
            params["access_token"] = token
            resp = requests.get(url, params=params, timeout=30)
        elif method == "POST":
            body["access_token"] = token
            resp = requests.post(url, data=body, timeout=30)
        elif method == "DELETE":
            resp = requests.delete(url, params={"access_token": token}, timeout=30)
        else:
            result["status"] = "SKIP"
            result["note"]   = f"Unsupported method: {method}"
            return result

        result["http_code"] = resp.status_code
        time.sleep(rate_delay)

        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}

        result["response"] = data

        if "error" in data:
            err        = data["error"]
            error_code = str(err.get("code", ""))
            error_sub  = str(err.get("error_subcode", ""))
            result["error_code"] = f"{error_code}/{error_sub}" if error_sub else error_code
            result["error_msg"]  = err.get("message", "")

            expected = str(ep.get("expected_error", ""))
            if expected and (expected in error_code or expected in error_sub):
                result["status"] = "EXPECTED_FAIL"
                result["note"]   = ep.get("note", f"Expected error {expected} — confirmed")
            else:
                result["status"] = "FAIL"
                ctx[f"failed_{ep['id']}"] = True
        else:
            result["status"] = "PASS"

            save_as = ep.get("save_id_as")
            if save_as:
                if "id" in data:
                    ctx[save_as] = data["id"]
                elif "images" in data:
                    first = next(iter(data["images"].values()), {})
                    ctx[save_as] = first.get("hash", "")
                elif "data" in data and isinstance(data["data"], list) and data["data"]:
                    ctx[save_as] = data["data"][0].get("id", "")

    except requests.exceptions.Timeout:
        result["status"]    = "FAIL"
        result["error_msg"] = "Request timed out after 30s"
        ctx[f"failed_{ep['id']}"] = True
    except requests.exceptions.ConnectionError as e:
        result["status"]    = "FAIL"
        result["error_msg"] = f"Connection error: {str(e)}"
        ctx[f"failed_{ep['id']}"] = True
    except Exception as e:
        result["status"]    = "FAIL"
        result["error_msg"] = f"Unexpected error: {str(e)}"
        ctx[f"failed_{ep['id']}"] = True

    return result


def generate_html(results, ctx, run_time):
    total    = len(results)
    passed   = sum(1 for r in results if r["status"] == "PASS")
    failed   = sum(1 for r in results if r["status"] == "FAIL")
    expected = sum(1 for r in results if r["status"] == "EXPECTED_FAIL")
    skipped  = sum(1 for r in results if r["status"] == "SKIP")

    groups = {}
    for r in results:
        groups.setdefault(r["group"], []).append(r)

    def status_badge(s):
        colors = {
            "PASS":          ("d1fae5", "065f46", "✓ PASS"),
            "FAIL":          ("fee2e2", "991b1b", "✗ FAIL"),
            "EXPECTED_FAIL": ("fef3c7", "92400e", "~ EXPECTED"),
            "SKIP":          ("f7f8fa", "57606a", "— SKIP"),
        }
        bg, fg, label = colors.get(s, ("f7f8fa", "57606a", s))
        return f'<span style="background:#{bg};color:#{fg};font-size:0.7rem;font-weight:700;padding:2px 8px;border-radius:10px;white-space:nowrap;">{label}</span>'

    def method_badge(m):
        colors = {"GET": ("dbeafe","1e40af"), "POST": ("d1fae5","065f46"), "DELETE": ("fee2e2","991b1b")}
        bg, fg = colors.get(m, ("f7f8fa","57606a"))
        return f'<span style="background:#{bg};color:#{fg};font-size:0.68rem;font-weight:700;padding:1px 6px;border-radius:3px;">{m}</span>'

    rows = ""
    for group, items in groups.items():
        g_pass  = sum(1 for r in items if r["status"] == "PASS")
        g_total = len(items)
        rows += f"""
        <tr style="background:#f7f8fa;">
          <td colspan="6" style="padding:8px 10px;font-weight:700;font-size:0.82rem;color:#1f2328;border:1px solid #e5e7eb;">
            {group} &nbsp;<span style="font-weight:400;color:#57606a;font-size:0.75rem;">({g_pass}/{g_total} passed)</span>
          </td>
        </tr>"""
        for r in items:
            resp_preview = ""
            if r["response"]:
                try:
                    preview = json.dumps(r["response"], indent=2)
                    if len(preview) > 500:
                        preview = preview[:500] + "\n... (truncated)"
                    resp_preview = f'<pre style="background:#f7f8fa;border:1px solid #e5e7eb;border-radius:4px;padding:8px;font-size:0.72rem;max-height:120px;overflow-y:auto;margin-top:4px;">{preview}</pre>'
                except Exception:
                    resp_preview = str(r["response"])[:200]

            error_info = ""
            if r["error_msg"]:
                error_info = f'<div style="font-size:0.75rem;color:#991b1b;margin-top:3px;">Error {r["error_code"]}: {r["error_msg"][:200]}</div>'

            note_info = ""
            if r["note"]:
                note_info = f'<div style="font-size:0.74rem;color:#57606a;font-style:italic;margin-top:2px;">{r["note"]}</div>'

            rows += f"""
        <tr>
          <td style="padding:7px 9px;border:1px solid #e5e7eb;color:#57606a;font-size:0.75rem;">{r["id"]}</td>
          <td style="padding:7px 9px;border:1px solid #e5e7eb;">{method_badge(r["method"])}</td>
          <td style="padding:7px 9px;border:1px solid #e5e7eb;font-size:0.82rem;">
            <strong>{r["name"]}</strong>
            <div style="font-size:0.72rem;color:#57606a;font-family:monospace;">{r.get("url","")}</div>
            {note_info}
          </td>
          <td style="padding:7px 9px;border:1px solid #e5e7eb;text-align:center;font-size:0.78rem;">{r["http_code"] or "—"}</td>
          <td style="padding:7px 9px;border:1px solid #e5e7eb;">{status_badge(r["status"])}</td>
          <td style="padding:7px 9px;border:1px solid #e5e7eb;font-size:0.78rem;max-width:300px;">
            {error_info}
            {resp_preview}
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\">
  <title>Meta Ads API — Validation Report</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
    body{{font-family:-apple-system,\"Segoe UI\",system-ui,sans-serif;font-size:14px;line-height:1.6;color:#1f2328;background:#fff;padding:32px 24px;}}
    .wrapper{{max-width:1100px;margin:0 auto;}}
    h1{{font-size:1.5rem;margin-bottom:4px;}}
    .meta{{color:#57606a;font-size:0.82rem;margin-bottom:24px;}}
    .cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:24px;}}
    .card{{border-radius:6px;padding:12px 14px;border:1px solid #e5e7eb;}}
    .card .n{{font-size:1.8rem;font-weight:700;}}
    .card .l{{font-size:0.72rem;color:#57606a;}}
    table{{width:100%;border-collapse:collapse;font-size:0.82rem;}}
    thead th{{background:#f7f8fa;font-weight:600;text-align:left;padding:8px 10px;border:1px solid #e5e7eb;color:#57606a;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;}}
    tbody tr:hover{{background:#fafbfc;}}
    hr{{border:none;border-top:1px solid #e5e7eb;margin:28px 0 12px;}}
    footer{{font-size:0.7rem;color:#57606a;text-align:center;padding-top:14px;border-top:1px solid #e5e7eb;margin-top:32px;}}
  </style>
</head>
<body>
<div class=\"wrapper\">
  <h1>Meta Ads API — Validation Report</h1>
  <p class=\"meta\">Account: <strong>{ctx.get("account_id","—")}</strong> &nbsp;|&nbsp; API Version: <strong>v26.0</strong> &nbsp;|&nbsp; Run time: <strong>{run_time}</strong> &nbsp;|&nbsp; Endpoints tested: <strong>{total}</strong></p>
  <div class=\"cards\">
    <div class=\"card\" style=\"background:#f0fdf4;border-color:#bbf7d0;\"><div class=\"n\" style=\"color:#16a34a;\">{passed}</div><div class=\"l\">Passed</div></div>
    <div class=\"card\" style=\"background:#fff1f2;border-color:#fecdd3;\"><div class=\"n\" style=\"color:#dc2626;\">{failed}</div><div class=\"l\">Failed</div></div>
    <div class=\"card\" style=\"background:#fffbeb;border-color:#fde68a;\"><div class=\"n\" style=\"color:#d97706;\">{expected}</div><div class=\"l\">Expected Failures</div></div>
    <div class=\"card\" style=\"background:#f7f8fa;border-color:#e5e7eb;\"><div class=\"n\" style=\"color:#57606a;\">{skipped}</div><div class=\"l\">Skipped</div></div>
    <div class=\"card\" style=\"background:#eff6ff;border-color:#bfdbfe;\"><div class=\"n\" style=\"color:#1d4ed8;\">{round(passed/total*100) if total else 0}%</div><div class=\"l\">Pass Rate</div></div>
  </div>
  <table>
    <thead><tr><th style=\"width:36px;\">#</th><th style=\"width:60px;\">Method</th><th>Endpoint</th><th style=\"width:60px;\">HTTP</th><th style=\"width:90px;\">Status</th><th>Response / Error</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <hr>
  <footer>connector-csc-metaads &mdash; Meta Ads API Validation Report &mdash; Made with IBM Bob</footer>
</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Meta Ads API Endpoint Validator")
    parser.add_argument("--token",    required=True,  help="Meta Graph API access token")
    parser.add_argument("--account",  required=True,  help="Ad account ID (e.g. act_3949033192058517)")
    parser.add_argument("--page",     required=False, default="", help="Facebook Page ID")
    parser.add_argument("--campaign", required=False, default="", help="Existing campaign ID for read tests")
    parser.add_argument("--adset",    required=False, default="", help="Existing ad set ID for read tests")
    parser.add_argument("--creative", required=False, default="", help="Existing creative ID for read tests")
    parser.add_argument("--delay",    required=False, default=0.3, type=float, help="Delay between requests (default 0.3s)")
    parser.add_argument("--dry-run",  action="store_true", help="Print endpoints without calling API")
    args = parser.parse_args()

    context.update({
        "account_id":   args.account,
        "page_id":      args.page,
        "campaign_id":  args.campaign,
        "adset_id":     args.adset,
        "creative_id":  args.creative,
    })

    with open(ENDPOINTS, "r") as f:
        endpoints = json.load(f)

    print(f"\n{'='*60}")
    print(f"  Meta Ads API Validation — connector-csc-metaads")
    print(f"  Account : {args.account}")
    print(f"  Endpoints: {len(endpoints)}")
    print(f"{'='*60}\n")

    if args.dry_run:
        for ep in endpoints:
            print(f"  [{ep['id']}] {ep['method']:<6} {ep['group']} — {ep['name']}")
        return

    results  = []
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for ep in endpoints:
        print(f"  [{ep['id']}] Testing: {ep['name']} ...", end=" ", flush=True)
        result = run_endpoint(ep, args.token, context, args.delay)
        results.append(result)
        icon = {"PASS": "✓", "FAIL": "✗", "EXPECTED_FAIL": "~", "SKIP": "—"}.get(result["status"], "?")
        msg  = f"  ← {result.get('error_msg','')[:80]}" if result["status"] == "FAIL" else ""
        print(f"{icon} {result['status']}{msg}")

    passed   = sum(1 for r in results if r["status"] == "PASS")
    failed   = sum(1 for r in results if r["status"] == "FAIL")
    expected = sum(1 for r in results if r["status"] == "EXPECTED_FAIL")
    skipped  = sum(1 for r in results if r["status"] == "SKIP")

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed | {failed} failed | {expected} expected failures | {skipped} skipped")
    print(f"  Pass rate: {round(passed/len(results)*100)}%")
    print(f"{'='*60}\n")

    os.makedirs(REPORT_DIR, exist_ok=True)

    json_path = os.path.join(REPORT_DIR, "validation_results.json")
    with open(json_path, "w") as f:
        json.dump({"run_time": run_time, "account_id": args.account,
                   "summary": {"total": len(results), "passed": passed, "failed": failed,
                               "expected_failures": expected, "skipped": skipped},
                   "results": results}, f, indent=2)

    html_path = os.path.join(REPORT_DIR, "validation_report.html")
    with open(html_path, "w") as f:
        f.write(generate_html(results, context, run_time))

    print(f"  Reports saved:")
    print(f"    JSON : {json_path}")
    print(f"    HTML : {html_path}\n")


if __name__ == "__main__":
    main()
