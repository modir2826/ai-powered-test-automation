# Task 2 — Plain-English to browser tests (Python + Playwright + OpenAI)

A small browser agent that reads a test case, inspects the live page, chooses one browser action at a time, and reports PASS / FAIL / BLOCKED. The model is not given a PHPTRAVELS-specific script; it receives the starting URL, user test, and a fresh page snapshot at each step.

## Requirements
- Python 3.10+
- OpenAI API key with access to the configured model
- Chromium installed by Playwright
- Internet access

## Setup (Windows PowerShell)
```powershell
cd task2-ai-test-runner
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
```
Edit `.env` and set `OPENAI_API_KEY`. `OPENAI_MODEL` defaults to `gpt-4o-mini`; change it to a model available to your API account if needed.

## Try on PHPTRAVELS
The current PHPTRAVELS demo page explains that demo access is obtained through a short access form and then displays role-specific credentials. A safe first smoke test is to verify that the access form/instructions are visible, rather than assume old shared credentials.

```powershell
python -m ai_test_runner.runner --url "https://phptravels.com/demo" --test "Open the PHPTRAVELS demo page and verify that the demo access request form or the heading 'Get your demo access' is displayed." --headed
```

The runner writes `reports/latest.json` and a screenshot beside it. Exit code is `0` only for PASS, `1` for FAIL/BLOCKED, and `2` for configuration errors.

## Try a different website / test
```powershell
python -m ai_test_runner.runner --url "https://example.com" --test "Verify that the page heading Example Domain is visible."
```
The test case and starting URL are supplied at runtime. There are no PHPTRAVELS selectors or credentials in the code.

## How it works
1. Playwright opens the supplied URL.
2. A compact snapshot records page URL/title and visible controls/headings with short text, labels, roles, types, placeholders, and link destinations.
3. The LLM selects exactly one next action from a constrained JSON action vocabulary.
4. The runner executes that action with Playwright and captures the result.
5. The page is re-snapshotted and the model decides the next step.
6. The model must explicitly return `done` with `passed=true` after the acceptance condition is verified. Otherwise the report is FAIL or BLOCKED.

Supported actions: click, fill, select, check, press, navigate (when requested), assert visible text, assert element visibility, and assert URL fragment.

## Security and scope
- Never put API keys in source control. `.env` is ignored by Git.
- The agent is instructed not to invent credentials or perform purchases, payments, message sending, destructive actions, or security bypasses.
- Use only demo/test accounts and synthetic data. PHPTRAVELS says its shared demo data is reset and warns against entering real customer information.
- Review generated actions before using the tool on a sensitive production website.

## Limitations (be honest in the interview)
- This is a prototype, not a production-grade autonomous QA platform. Model decisions can be wrong or inconsistent.
- It depends on an LLM API key, network access, and token/API availability; API usage may incur cost.
- A text-only DOM snapshot can miss canvas, inaccessible controls, complex shadow DOM, iframes, virtualized lists, and visual-only interactions.
- The current version targets visible DOM elements and common HTML controls. File uploads, multi-tab flows, CAPTCHA, MFA, custom widgets, and complex date pickers need additional adapters.
- A model can misinterpret ambiguous instructions. The report includes steps and a screenshot so a human can review outcomes.
- The agent is intentionally conservative: when it cannot identify a control or needs credentials/unsafe actions, it should block instead of guessing.
- For robust evaluation, add deterministic assertions, retries, trace/video capture, schema validation, domain allowlists, and human approval for risky actions.

## Project layout
```text
ai_test_runner/
  runner.py       # LLM planning loop + Playwright action executor
.env.example      # environment variable template
requirements.txt
README.md
```
