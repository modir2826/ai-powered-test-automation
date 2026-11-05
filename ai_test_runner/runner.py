from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from playwright.async_api import async_playwright, Page

SYSTEM_PROMPT = """
You are an experienced web QA test planner controlling a real browser.
Given a user's natural-language test case and a compact snapshot of the CURRENT page,
choose exactly ONE next action. Use only evidence visible in the snapshot.
Never invent credentials, personal data, URLs, selectors, or success states.
If the test requires credentials not supplied by the user, return action="blocked"
with a concise reason. Do not bypass authentication or security controls.
Do not make purchases, submit payment, send messages, delete data, or perform other
irreversible actions. If such an action is required, return blocked.
Use semantic accessible names/labels/roles where possible, and use the provided
element references (e.g. [e7]) for precise targeting.
Allowed actions:
- click: {"action":"click","target":"eN","reason":"..."}
- fill: {"action":"fill","target":"eN","value":"...","reason":"..."}
- select: {"action":"select","target":"eN","value":"...","reason":"..."}
- check: {"action":"check","target":"eN","reason":"..."}
- press: {"action":"press","target":"eN","value":"Enter","reason":"..."}
- goto: {"action":"goto","value":"https://...","reason":"..."} (only if task explicitly requires navigation)
- assert_text: {"action":"assert_text","value":"expected visible text","reason":"..."}
- assert_visible: {"action":"assert_visible","target":"eN","reason":"..."}
- assert_url: {"action":"assert_url","value":"substring or regex-free expected URL fragment","reason":"..."}
- done: {"action":"done","passed":true|false,"summary":"..."}
- blocked: {"action":"blocked","summary":"..."}
Return valid JSON only. Do not return markdown.
Prefer checking a clearly visible success indicator/dashboard heading after the requested action.
If no relevant target exists, do not guess; return blocked or failed done.
"""

def compact_snapshot(page: Page) -> str:
    """Expose accessible-ish page structure without dumping scripts or full HTML."""
    return ""

async def build_snapshot(page: Page) -> str:
    data = await page.locator("body").evaluate(r"""
    body => {
      const norm = s => (s || '').replace(/\s+/g, ' ').trim();
      const visible = el => {
        const r = el.getBoundingClientRect(), st = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
      };
      const nodes = [...body.querySelectorAll('a,button,input,textarea,select,[role],h1,h2,h3,label')].filter(visible).slice(0,180);
      const filtered = nodes.filter(el => {
        const txt = norm(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '');
        const lab = el.labels ? [...el.labels].map(x=>norm(x.innerText)).join(' ') : '';
        return txt || lab || ['input','select','textarea'].includes(el.tagName.toLowerCase());
      });
      return filtered.map((el,i) => {
        const tag = el.tagName.toLowerCase();
        const type = el.getAttribute('type') || '';
        const role = el.getAttribute('role') || '';
        const label = el.labels ? [...el.labels].map(x=>norm(x.innerText)).join(' ') : '';
        const text = norm(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '');
        const name = norm(el.getAttribute('aria-label') || label || el.getAttribute('placeholder') || el.getAttribute('name') || text);
        const href = el.getAttribute('href') || '';
        const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';
        return {ref:'e'+i, tag, type, role, name:name.slice(0,100), text:text.slice(0,120),
                href:href.slice(0,180), value:(el.value||'').slice(0,100), disabled};
      });
    }
    """)
    return json.dumps({
        "url": page.url,
        "title": await page.title(),
        "elements": data
    }, ensure_ascii=False)

def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    return json.loads(text)

async def resolve_ref(page: Page, ref: str):
    if not re.fullmatch(r"e\d+", ref):
        raise ValueError(f"Invalid element reference: {ref}")
    index = int(ref[1:])
    # The snapshot is regenerated before each action, so the positional index
    # is scoped to the current DOM state rather than a stale selector.
    # Match the same visible-node ordering used in snapshot via a temporary DOM attribute.
    await page.evaluate(r"""() => {
      document.querySelectorAll('[data-ai-ref]').forEach(e=>e.removeAttribute('data-ai-ref'));
      const visible = el => { const r=el.getBoundingClientRect(),s=getComputedStyle(el);
        return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'; };
      const nodes = [...document.body.querySelectorAll('a,button,input,textarea,select,[role],h1,h2,h3,label')]
        .filter(visible).slice(0,180).filter(el => {
          const norm = s => (s || '').replace(/\\s+/g,' ').trim();
          const txt = norm(el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '');
          const lab = el.labels ? [...el.labels].map(x=>norm(x.innerText)).join(' ') : '';
          return txt || lab || ['input','select','textarea'].includes(el.tagName.toLowerCase());
        });
      nodes.forEach((el,i)=>el.setAttribute('data-ai-ref','e'+i));
    }""")
    return page.locator(f'[data-ai-ref="{ref}"]')

async def execute_action(page: Page, action: dict[str, Any]) -> str:
    kind = action.get("action")
    reason = action.get("reason", "")
    if kind == "goto":
        value = str(action.get("value", ""))
        if not value.startswith(("https://", "http://")):
            raise ValueError("Navigation URL must be http(s).")
        await page.goto(value, wait_until="domcontentloaded", timeout=30000)
    elif kind in {"click", "fill", "select", "check", "press", "assert_visible"}:
        target = await resolve_ref(page, str(action.get("target", "")))
        if kind == "click":
            await target.click(timeout=10000)
        elif kind == "fill":
            await target.fill(str(action.get("value", "")), timeout=10000)
        elif kind == "select":
            await target.select_option(label=str(action.get("value", "")), timeout=10000)
        elif kind == "check":
            await target.check(timeout=10000)
        elif kind == "press":
            await target.press(str(action.get("value", "Enter")), timeout=10000)
        elif kind == "assert_visible":
            if not await target.is_visible():
                raise AssertionError(f"Expected {action.get('target')} to be visible")
    elif kind == "assert_text":
        expected = str(action.get("value", ""))
        if not expected:
            raise ValueError("assert_text needs expected text")
        try:
            await page.get_by_text(expected, exact=False).first.wait_for(state="visible", timeout=3000)
        except Exception:
            raise AssertionError(f"Expected visible text not found: {expected}")
    elif kind == "assert_url":
        expected = str(action.get("value", ""))
        if expected not in page.url:
            raise AssertionError(f"Expected URL to contain '{expected}', got '{page.url}'")
    else:
        raise ValueError(f"Unsupported action: {kind}")
    await page.wait_for_timeout(500)
    return f"Executed {kind}. {reason}".strip()

async def run_test(url: str, test_case: str, headless: bool, max_steps: int, output: Path) -> int:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY is missing. Copy .env.example to .env and add your API key.")
        return 2
    client = OpenAI(api_key=api_key)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    history: list[dict[str, str]] = []
    result: dict[str, Any] = {"status": "FAIL", "test_case": test_case, "url": url, "steps": []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1365, "height": 900})
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            for step_no in range(1, max_steps + 1):
                snapshot = await build_snapshot(page)
                user_message = (
                    f"TEST CASE: {test_case}\n"
                    f"START URL: {url}\n"
                    f"CURRENT PAGE SNAPSHOT:\n{snapshot}\n"
                    f"PREVIOUS ACTIONS/RESULTS:\n{json.dumps(history[-8:], ensure_ascii=False)}\n"
                    "Choose the next single action. If the acceptance condition has been verified, return done passed=true."
                )
                response = client.chat.completions.create(
                    model=model,
                    temperature=0,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                )
                action = parse_json(response.choices[0].message.content or "{}")
                kind = action.get("action")
                if kind == "done":
                    result["status"] = "PASS" if action.get("passed") is True else "FAIL"
                    result["summary"] = action.get("summary", "Model completed the test.")
                    break
                if kind == "blocked":
                    result["status"] = "BLOCKED"
                    result["summary"] = action.get("summary", "The test needs information or an unsupported action.")
                    break
                try:
                    message = await execute_action(page, action)
                    history.append({"step": str(step_no), "action": json.dumps(action), "result": message})
                    result["steps"].append({"step": step_no, "action": action, "result": message})
                except Exception as exc:
                    history.append({"step": str(step_no), "action": json.dumps(action), "result": f"ERROR: {type(exc).__name__}: {exc}"})
                    result["steps"].append({"step": step_no, "action": action, "error": str(exc)})
                    # Give the model a chance to recover once using fresh page state.
                    if step_no == max_steps:
                        result["summary"] = f"Step failed: {exc}"
            else:
                result["summary"] = f"Stopped after maximum {max_steps} actions without a verified pass."
            result["final_url"] = page.url
            try:
                await page.screenshot(path=str(output.with_suffix(".png")), full_page=True)
                result["screenshot"] = str(output.with_suffix(".png"))
            except Exception:
                pass
        except Exception as exc:
            result["summary"] = f"Runner error: {type(exc).__name__}: {exc}"
            result["final_url"] = page.url
        finally:
            await browser.close()

    result["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nReport saved: {output}")
    return 0 if result["status"] == "PASS" else 1

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Turn plain-English web test cases into browser actions.")
    parser.add_argument("--url", required=True, help="Starting website URL")
    parser.add_argument("--test", required=True, help="Plain-English test case")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--report", default="reports/latest.json")
    args = parser.parse_args()
    code = asyncio.run(run_test(args.url, args.test, not args.headed, args.max_steps, Path(args.report)))
    raise SystemExit(code)

if __name__ == "__main__":
    main()
