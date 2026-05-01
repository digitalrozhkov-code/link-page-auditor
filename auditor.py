#!/usr/bin/env python3
"""
Чек-лист аудита страниц для проставления ссылок
Автоматическая проверка по всем критериям чек-листа
"""

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False


# ─────────────────────────── Data structures ────────────────────────────────

@dataclass
class CheckResult:
    passed: bool
    detail: str
    warning: bool = False  # не провал, но есть нюанс


@dataclass
class PageAudit:
    url: str
    timestamp: str = ""
    # Блок 1: Индексация
    check_robots_txt: CheckResult = None
    check_meta_robots: CheckResult = None
    check_x_robots: CheckResult = None
    check_site_operator: CheckResult = None
    # Блок 2: Канонічність
    check_canonical: CheckResult = None
    # Блок 3: Посилання
    check_link_in_html: CheckResult = None
    check_link_not_js: CheckResult = None
    check_no_redirect_gateway: CheckResult = None
    # Блок 4: Загальний висновок
    verdict: str = ""
    issues: list = field(default_factory=list)

    def to_dict(self):
        d = {"url": self.url, "timestamp": self.timestamp, "verdict": self.verdict, "issues": self.issues}
        for attr in [
            "check_robots_txt", "check_meta_robots", "check_x_robots",
            "check_site_operator", "check_canonical",
            "check_link_in_html", "check_link_not_js", "check_no_redirect_gateway",
        ]:
            val = getattr(self, attr)
            if val:
                d[attr] = {"passed": val.passed, "detail": val.detail, "warning": val.warning}
            else:
                d[attr] = None
        return d


# ─────────────────────────── Helpers ────────────────────────────────────────

def get_domain_path(url: str) -> tuple[str, str]:
    p = urlparse(url)
    return p.netloc, p.path


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url


# ─────────────────────────── Checker class ──────────────────────────────────

class LinkAuditor:

    GOOGLE_SEARCH_URL = "https://www.google.com/search"
    TIMEOUT = 20_000  # ms для Playwright

    def __init__(self, headless: bool = True, verbose: bool = True):
        self.headless = headless
        self.verbose = verbose
        self._browser = None
        self._context = None

    def log(self, msg: str):
        if self.verbose:
            print(f"  {msg}")

    # ──────────── Playwright helpers ────────────────────────────────────────

    async def _get_page_source(self, url: str) -> tuple[str, dict]:
        """
        Повертає (html_вихідний_код, response_headers).
        Використовує Playwright з відключеним JS рендерингом для отримання
        саме вихідного HTML (аналог Ctrl+U), але також запам'ятовує
        response headers.
        """
        page = await self._context.new_page()
        headers = {}
        raw_source = ""

        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.TIMEOUT)
            if resp:
                headers = await resp.all_headers()

            # Отримуємо view-source HTML (як Ctrl+U) — без JS-трансформацій
            # page.content() повертає живий DOM, а нам треба оригінал з сервера
            # Тому робимо додатковий запит через requests (швидше і точніше)
            raw_source = await page.content()  # живий DOM для деяких перевірок
        except Exception as e:
            self.log(f"⚠ Playwright помилка: {e}")
        finally:
            await page.close()

        return raw_source, headers

    async def _fetch_raw_source(self, url: str):
        """
        Отримуємо вихідний HTML без JS (аналог Ctrl+U).
        Спочатку пробуємо requests, якщо порожньо або заблоковано — Playwright.
        """
        # Спроба 1: requests (швидше, дає справжній вихідний HTML)
        try:
            r = requests.get(
                url,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                },
                allow_redirects=True,
            )
            html = r.text
            if html and "<head" in html.lower() and r.status_code == 200:
                self.log(f"  ↳ HTML отримано через requests ({len(html)} байт)")
                return html, dict(r.headers), r.url
            else:
                self.log(f"  ↳ requests повернув {r.status_code}, пробуємо Playwright...")
        except Exception as e:
            self.log(f"  ↳ requests помилка: {e}, пробуємо Playwright...")

        # Спроба 2: Playwright (з stealth якщо доступний)
        try:
            page = await self._context.new_page()
            if HAS_STEALTH:
                await stealth_async(page)
            resp_headers = {}
            final_url = url

            resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.TIMEOUT)
            if resp:
                resp_headers = await resp.all_headers()
                final_url = resp.url

            await asyncio.sleep(1)
            try:
                raw = await page.evaluate("""async () => {
                    try {
                        const r = await fetch(window.location.href, {cache: 'no-store'});
                        return await r.text();
                    } catch(e) {
                        return document.documentElement.outerHTML;
                    }
                }""")
                if raw and "<head" in raw.lower():
                    self.log(f"  ↳ HTML отримано через Playwright fetch ({len(raw)} байт)")
                    await page.close()
                    return raw, resp_headers, final_url
            except Exception:
                pass

            content = await page.content()
            self.log(f"  ↳ HTML отримано через Playwright DOM ({len(content)} байт)")
            await page.close()
            return content, resp_headers, final_url

        except Exception as e:
            self.log(f"⚠ Playwright помилка: {e}")
            return "", {}, url

    # ──────────── Check: robots.txt ─────────────────────────────────────────

    async def check_robots_txt(self, url: str) -> CheckResult:
        self.log("🔍 Перевірка robots.txt ...")
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        path = parsed.path or "/"

        try:
            r = requests.get(robots_url, timeout=10)
            content = r.text.lower()

            # Шукаємо disallow для цього шляху або для всього сайту
            # Проста евристика: шукаємо Disallow: / або Disallow: <path>
            lines = content.splitlines()
            in_ua_block = False
            disallowed_paths = []

            for line in lines:
                line = line.strip()
                if line.startswith("user-agent:"):
                    ua = line.split(":", 1)[1].strip()
                    in_ua_block = ua in ("*", "googlebot")
                elif in_ua_block and line.startswith("disallow:"):
                    dp = line.split(":", 1)[1].strip()
                    if dp:
                        disallowed_paths.append(dp)

            blocked = any(
                path.startswith(dp) or dp == "/"
                for dp in disallowed_paths
            )

            if blocked:
                return CheckResult(
                    passed=False,
                    detail=f"Заблоковано в robots.txt: {disallowed_paths}",
                )
            return CheckResult(passed=True, detail="Шлях не заблоковано в robots.txt")

        except Exception as e:
            return CheckResult(passed=True, detail=f"robots.txt недоступний або помилка: {e}", warning=True)

    # ──────────── Check: meta robots ────────────────────────────────────────

    def check_meta_robots(self, soup: BeautifulSoup) -> CheckResult:
        self.log("🔍 Перевірка meta robots ...")
        head = soup.find("head")
        if not head:
            return CheckResult(passed=True, detail="Тег <head> не знайдено", warning=True)

        for tag in head.find_all("meta", attrs={"name": re.compile(r"robots", re.I)}):
            content = tag.get("content", "").lower()
            if "noindex" in content:
                return CheckResult(
                    passed=False,
                    detail=f'<meta name="robots" content="{tag.get("content")}"> — сторінка закрита від індексації',
                )

        return CheckResult(passed=True, detail="noindex у meta robots відсутній")

    # ──────────── Check: X-Robots-Tag header ────────────────────────────────

    def check_x_robots_header(self, headers: dict) -> CheckResult:
        self.log("🔍 Перевірка X-Robots-Tag заголовка ...")
        for key, val in headers.items():
            if key.lower() == "x-robots-tag":
                if "noindex" in val.lower():
                    return CheckResult(
                        passed=False,
                        detail=f"X-Robots-Tag: {val} — сторінка закрита від індексації",
                    )
                return CheckResult(passed=True, detail=f"X-Robots-Tag: {val} (без noindex)")
        return CheckResult(passed=True, detail="Заголовок X-Robots-Tag відсутній")

    # ──────────── Check: canonical ──────────────────────────────────────────

    def check_canonical(self, soup: BeautifulSoup, url: str, raw_html: str) -> CheckResult:
        self.log("🔍 Перевірка canonical ...")

        head = soup.find("head")
        canonical_tag = None
        if head:
            canonical_tag = head.find("link", rel=lambda r: r and "canonical" in r)

        # Перевіряємо, чи canonical виводиться через JS (відсутній у вихідному коді)
        # Шукаємо в raw HTML (без JS) через regex
        raw_has_canonical = bool(re.search(r'rel=["\']canonical["\']', raw_html, re.I)
                                  or re.search(r'canonical.*href', raw_html, re.I))

        if not canonical_tag:
            if raw_has_canonical:
                return CheckResult(
                    passed=False,
                    detail="Canonical присутній у живому DOM, але відсутній у вихідному HTML → виводиться через JS (не зараховується)",
                )
            return CheckResult(
                passed=False,
                detail="Тег canonical відсутній — сторінка не помічена як канонічна",
            )

        canonical_href = canonical_tag.get("href", "").strip()

        # Нормалізуємо для порівняння
        parsed_url = urlparse(url)
        parsed_canonical = urlparse(canonical_href)

        # Якщо canonical відносний — розгортаємо
        if not parsed_canonical.scheme:
            canonical_href = urljoin(url, canonical_href)
            parsed_canonical = urlparse(canonical_href)

        url_clean = parsed_url._replace(fragment="").geturl().rstrip("/")
        can_clean = parsed_canonical._replace(fragment="").geturl().rstrip("/")

        if url_clean == can_clean:
            return CheckResult(passed=True, detail=f"Canonical відповідає URL: {canonical_href}")

        return CheckResult(
            passed=False,
            detail=f"Canonical вказує на іншу сторінку: {canonical_href} (URL: {url})",
            warning=True,  # не критично, якщо canonical веде туди ж по суті
        )

    # ──────────── Check: посилання в HTML (не JS) ───────────────────────────

    def check_link_in_raw_html(self, raw_html: str, target_url: str = None) -> CheckResult:
        """
        Перевіряємо, що посилання є у вихідному HTML, а не виводиться скриптом.
        Якщо target_url не вказано — перевіряємо загальну наявність <a href> тегів.
        """
        self.log("🔍 Перевірка наявності посилань у вихідному коді ...")

        if target_url:
            # Шукаємо конкретне посилання
            pattern = re.compile(
                r'<a\s[^>]*href=["\']' + re.escape(target_url) + r'["\'][^>]*>',
                re.I | re.S,
            )
            if pattern.search(raw_html):
                return CheckResult(passed=True, detail=f"Посилання на {target_url} знайдено у вихідному HTML")
            return CheckResult(
                passed=False,
                detail=f"Посилання на {target_url} відсутнє у вихідному HTML (можливо виводиться через JS)",
            )

        # Загальна перевірка
        a_tags = re.findall(r'<a\s[^>]*href=["\'][^"\']+["\'][^>]*>', raw_html, re.I | re.S)
        count = len(a_tags)
        return CheckResult(
            passed=True,
            detail=f"Знайдено {count} тегів <a href> у вихідному HTML",
        )

    # ──────────── Check: onclick / JS redirect ──────────────────────────────

    def check_no_js_links(self, soup: BeautifulSoup, raw_html: str) -> CheckResult:
        self.log("🔍 Перевірка JS-посилань (onclick/window.open) ...")
        issues = []

        # Шукаємо href="javascript:..."
        js_href = re.findall(r'<a\s[^>]*href=["\']javascript:[^"\']+["\'][^>]*>', raw_html, re.I | re.S)
        if js_href:
            issues.append(f"href=javascript: знайдено ({len(js_href)} шт.)")

        # Шукаємо onclick="window.open..."
        js_onclick = re.findall(r'onclick=["\'][^"\']*window\.open[^"\']*["\']', raw_html, re.I)
        if js_onclick:
            issues.append(f"onclick=window.open знайдено ({len(js_onclick)} шт.)")

        if issues:
            return CheckResult(
                passed=False,
                detail="Посилання виводяться через JS: " + "; ".join(issues),
            )
        return CheckResult(passed=True, detail="JS-посилань (onclick/javascript:href) не знайдено")

    # ──────────── Check: redirect gateway ───────────────────────────────────

    def check_no_redirect_gateway(self, soup: BeautifulSoup, raw_html: str) -> CheckResult:
        """Перевіряємо, чи немає проксі/редиректу через зовнішній шлюз."""
        self.log("🔍 Перевірка редиректів через зовнішній шлюз ...")

        # Шукаємо підозрілі паттерни: href містить redirect, go.php, out.php тощо
        gateway_patterns = re.compile(
            r'href=["\'][^"\']*(?:redirect|go\.php|out\.php|click\.php|exit\.php|'
            r'outgoing|external\?url=|url=https?)[^"\']*["\']',
            re.I,
        )
        matches = gateway_patterns.findall(raw_html)
        if matches:
            return CheckResult(
                passed=False,
                detail=f"Знайдено посилання через редирект-шлюз: {matches[:3]}",
            )
        return CheckResult(passed=True, detail="Редирект-шлюзів не знайдено")

    # ──────────── Site: operator check (через requests до Google) ───────────

    async def check_site_operator(self, url: str) -> CheckResult:
        """
        Перевіряємо site: оператор через Google Search.
        Примітка: Google може блокувати автоматичні запити.
        Використовуємо Playwright з рандомними затримками.
        """
        self.log("🔍 Перевірка оператора site: через Google ...")

        parsed = urlparse(url)
        # Для site: оператора прибираємо fragment і query
        site_query = f"site:{parsed.netloc}{parsed.path}"
        if parsed.query:
            site_query += f"?{parsed.query}"

        search_url = f"https://www.google.com/search?q={requests.utils.quote(site_query)}&hl=uk"

        page = await self._context.new_page()
        if HAS_STEALTH:
            await stealth_async(page)
        result_text = ""
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=self.TIMEOUT)
            await asyncio.sleep(2)  # затримка щоб уникнути блокування
            content = await page.content()

            # Шукаємо результати
            soup = BeautifulSoup(content, "lxml")

            # Перевіряємо на CAPTCHA
            if "captcha" in content.lower() or "unusual traffic" in content.lower():
                return CheckResult(
                    passed=True,
                    detail="⚠ Google показав CAPTCHA — перевірте вручну",
                    warning=True,
                )

            # Перевіряємо чи є результати
            result_stats = soup.find(id="result-stats")
            if result_stats:
                stats_text = result_stats.get_text()
                if any(c.isdigit() for c in stats_text):
                    return CheckResult(
                        passed=True,
                        detail=f"Сторінка доступна в Google: {stats_text.strip()[:100]}",
                    )

            # Альтернативна перевірка
            no_results_indicators = [
                "did not match any documents",
                "не відповідає жодним документам",
                "keine ergebnisse",
                "0 results",
                "No results found",
            ]
            page_text = soup.get_text().lower()
            if any(ind.lower() in page_text for ind in no_results_indicators):
                return CheckResult(
                    passed=False,
                    detail="Сторінка НЕ знайдена через оператор site: — не індексується",
                )

            # Шукаємо хоча б один результат що містить наш домен
            links = soup.find_all("a", href=True)
            domain_found = any(parsed.netloc in (lnk.get("href", "") or "") for lnk in links)
            if domain_found:
                return CheckResult(passed=True, detail="Домен знайдено в результатах Google")

            return CheckResult(
                passed=True,
                detail="Результати site: неоднозначні — рекомендуємо перевірити вручну",
                warning=True,
            )

        except Exception as e:
            return CheckResult(
                passed=True,
                detail=f"Не вдалось перевірити site: — перевірте вручну ({e})",
                warning=True,
            )
        finally:
            await page.close()

    # ──────────── Main audit ────────────────────────────────────────────────

    async def audit_url(self, url: str) -> PageAudit:
        url = normalize_url(url)
        audit = PageAudit(url=url, timestamp=datetime.now().isoformat())

        print(f"\n{'='*60}")
        print(f"🌐 Аудит: {url}")
        print(f"{'='*60}")

        # 1. Отримуємо вихідний HTML (через requests — аналог Ctrl+U)
        raw_html, resp_headers, final_url = await self._fetch_raw_source(url)

        if not raw_html:
            audit.verdict = "❌ ПОМИЛКА"
            audit.issues.append("Сторінка недоступна")
            return audit

        soup = BeautifulSoup(raw_html, "lxml")

        # 2. Запускаємо всі перевірки
        audit.check_robots_txt = await self.check_robots_txt(url)
        audit.check_meta_robots = self.check_meta_robots(soup)
        audit.check_x_robots = self.check_x_robots_header(resp_headers)
        audit.check_site_operator = await self.check_site_operator(url)
        audit.check_canonical = self.check_canonical(soup, final_url, raw_html)
        audit.check_link_in_html = self.check_link_in_raw_html(raw_html)
        audit.check_link_not_js = self.check_no_js_links(soup, raw_html)
        audit.check_no_redirect_gateway = self.check_no_redirect_gateway(soup, raw_html)

        # 3. Формуємо вердикт
        checks = [
            audit.check_robots_txt,
            audit.check_meta_robots,
            audit.check_x_robots,
            audit.check_site_operator,
            audit.check_canonical,
            audit.check_link_in_html,
            audit.check_link_not_js,
            audit.check_no_redirect_gateway,
        ]

        hard_fails = [c for c in checks if c and not c.passed and not c.warning]
        warnings = [c for c in checks if c and not c.passed and c.warning]

        if hard_fails:
            audit.verdict = "❌ НЕ ПІДХОДИТЬ"
            audit.issues = [c.detail for c in hard_fails]
        elif warnings:
            audit.verdict = "⚠️ ПОТРЕБУЄ РУЧНОЇ ПЕРЕВІРКИ"
            audit.issues = [c.detail for c in warnings]
        else:
            audit.verdict = "✅ ПІДХОДИТЬ"

        return audit

    async def audit_urls(self, urls: list[str]) -> list[PageAudit]:
        async with async_playwright() as pw:
            self._browser = await pw.chromium.launch(headless=self.headless)
            self._context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="uk-UA",
            )
            results = []
            for url in urls:
                audit = await self.audit_url(url)
                results.append(audit)
                # Пауза між запитами щоб не виглядати як бот
                await asyncio.sleep(3)

            await self._browser.close()
        return results


# ─────────────────────────── Report ─────────────────────────────────────────

def print_report(audits: list[PageAudit]):
    print(f"\n\n{'='*60}")
    print("📊 ЗВЕДЕНИЙ ЗВІТ")
    print(f"{'='*60}")

    for a in audits:
        short_url = a.url[:70] + "..." if len(a.url) > 70 else a.url
        print(f"\n{a.verdict}")
        print(f"  URL: {short_url}")
        if a.issues:
            for issue in a.issues:
                print(f"  • {issue}")

        # Деталі по кожній перевірці
        checks_map = {
            "robots.txt": a.check_robots_txt,
            "meta robots": a.check_meta_robots,
            "X-Robots-Tag": a.check_x_robots,
            "site: оператор": a.check_site_operator,
            "canonical": a.check_canonical,
            "links in HTML": a.check_link_in_html,
            "no JS links": a.check_link_not_js,
            "no gateway redirect": a.check_no_redirect_gateway,
        }
        for name, chk in checks_map.items():
            if chk:
                icon = "✅" if chk.passed else ("⚠️" if chk.warning else "❌")
                print(f"    {icon} [{name}] {chk.detail}")


def save_json_report(audits: list[PageAudit], path: str = "audit_report.json"):
    data = [a.to_dict() for a in audits]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON звіт збережено: {path}")


def save_text_report(audits: list[PageAudit], path: str = "audit_report.txt"):
    lines = [f"Аудит посилань — {datetime.now().strftime('%Y-%m-%d %H:%M')}", "=" * 60]
    for a in audits:
        lines.append(f"\n{a.verdict}")
        lines.append(f"URL: {a.url}")
        if a.issues:
            for issue in a.issues:
                lines.append(f"  • {issue}")
        checks_map = {
            "robots.txt": a.check_robots_txt,
            "meta robots": a.check_meta_robots,
            "X-Robots-Tag": a.check_x_robots,
            "site: оператор": a.check_site_operator,
            "canonical": a.check_canonical,
            "links in HTML": a.check_link_in_html,
            "no JS links": a.check_link_not_js,
            "no gateway redirect": a.check_no_redirect_gateway,
        }
        for name, chk in checks_map.items():
            if chk:
                icon = "OK" if chk.passed else ("WARN" if chk.warning else "FAIL")
                lines.append(f"  [{icon}] {name}: {chk.detail}")
        lines.append("-" * 60)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"💾 Текстовий звіт збережено: {path}")


# ─────────────────────────── Entry point ────────────────────────────────────

async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Аудит сторінок для проставлення посилань"
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="URL для перевірки (можна передати кілька)",
    )
    parser.add_argument(
        "--file",
        "-f",
        help="Текстовий файл з URL (по одному на рядок)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="audit_report",
        help="Базова назва файлу звіту (без розширення)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Запускати браузер у headless режимі (за замовчуванням)",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        default=False,
        help="Показати браузер під час перевірки",
    )
    args = parser.parse_args()

    urls = list(args.urls)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

    if not urls:
        print("Вкажіть URL для перевірки.")
        print("Приклад: python auditor.py https://example.com/forum/topic")
        print("Або: python auditor.py --file urls.txt")
        sys.exit(1)

    headless = not args.show_browser
    auditor = LinkAuditor(headless=headless)
    audits = await auditor.audit_urls(urls)

    print_report(audits)
    save_json_report(audits, f"{args.output}.json")
    save_text_report(audits, f"{args.output}.txt")

    # Підсумок
    total = len(audits)
    passed = sum(1 for a in audits if a.verdict.startswith("✅"))
    failed = sum(1 for a in audits if a.verdict.startswith("❌"))
    warned = sum(1 for a in audits if a.verdict.startswith("⚠"))
    print(f"\n📈 Підсумок: {total} перевірено | ✅ {passed} підходить | ⚠️ {warned} перевірити | ❌ {failed} не підходить")


if __name__ == "__main__":
    asyncio.run(main())
