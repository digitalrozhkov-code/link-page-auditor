# 🔍 Link Page Auditor

Automatic page review before adding links, based on an SEO audit checklist.

---

## 📋 What the script checks

| # | Verification | Description |
|---|----------|----------|
| 1 | **robots.txt** | Is the path blocked by a directive? `Disallow` |
| 2 | **meta robots noindex** | Is there a `noindex` attribute in the `<meta name="robots">` tag in the `<head>` |
| 3 | **X-Robots-Tag** | Is there a `noindex` in the server's HTTP response header? |
| 4 | **Site operator:** | Is this page indexed by Google? |
| 5 | **Canonical** | Does `<link rel="canonical">` match the page's URL? Determines whether the canonical URL is set via JavaScript (not counted) |
| 6 | **HTML Links** | Are there any `<a href>` tags in the source code (not the DOM)? |
| 7 | **JS links** | Are there any `onclick=window.open(...)` or `href=javascript:` |
| 8 | **Redirect gateway** | Is there a redirect via an external gateway (`go.php`, `redirect`, `out.php`, etc.) |

---

## ⚙️ Installation

### Requirements
- Python 3.9+
- Windows / macOS / Linux

### Steps

```bash
# 1. Clone a repository
git clone https://github.com/ВАШ_ЮЗЕРНЕЙМ/link-page-auditor.git
cd link-page-auditor

# 2. Set dependencies
pip install playwright requests beautifulsoup4 lxml playwright-stealth

# 3. Install the Chromium browser
python -m playwright install chromium
```

---

## 🚀 Launch

### A single URL
```bash
python auditor.py https://forum.example.com/topic/123
```

### Multiple URLs at once
```bash
python auditor.py https://site1.com/page https://site2.com/forum/thread
```

### List of URLs from a file
Create a file named `urls.txt`—one URL per line; lines starting with `#` are ignored:

Then run:
```bash
python auditor.py --file urls.txt
```

### Specify the report name
```bash
python auditor.py --file urls.txt --output my_report
```

### Show the browser during testing (useful for debugging)
```bash
python auditor.py --show-browser https://example.com/page
```

---

## 📊 Results

After the check, two files are created:
- `audit_report.txt` — readable text report
- `audit_report.json` — machine-readable format for integrations

### Verdicts
| Verdict | Meaning |
|---------|----------|
| ✅ APPROVED | All checks have been passed—you may post the link |
| ⚠️ REQUIRES MANUAL VERIFICATION | There are warnings (for example, Google displayed a CAPTCHA) |
| ❌ NOT RECOMMENDED | There are critical issues—do not post |

### Example of console output

============================================================
🌐 Audit: https://forum.gazeta.pl/forum/w,272,128030289,128030289,Sports_Betting.html
❌ NOT SUITABLE
URL: https://forum.gazeta.pl/forum/w,272,128030289,128030289,Sports_Betting.html
• Not accessible via the site operator: — not indexed
• Redirect via an external gateway
✅ [robots.txt] The path is not blocked
❌ [site: оператор] Page not found on Google
❌ [no gateway redirect] Links found via a redirect gateway
📈 Total: 5 checked | ✅ 2 подходит | ⚠️ 1 check | ❌ 2 not suitable

---

## 🔧 How the script works

1. **requests** — loads the page’s source HTML (equivalent to `Ctrl+U` in a browser) without executing JavaScript
2. **Playwright + stealth** — used as a fallback method for protected sites (Cloudflare, etc.), as well as for checking via Google
3. **BeautifulSoup** — parses the HTML and extracts the necessary tags

> **Important:** To check the `site:` operator, the script queries Google — with frequent requests, Google may display a CAPTCHA. In this case, the result will be marked as ⚠️ and will require manual verification.
