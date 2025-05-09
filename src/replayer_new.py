#!/usr/bin/env python3
# replayer.py — Selenium-based re‑player with fast mode, random UA, cookies & proxy support
# v3 — 2025‑04‑29: «SERP‑aware»
# ------------------------------------------------------------
# Изменения:
#   ДАННАЯ ВЕРСИЯ REPLAYER.PY ИСПОЛЬЗУЕТ БОЛЕЕ ПРОДВИНУТУЮ ВЕРСИЮ КЛИКОВ В ВЫДАЧЕ
#   • «navigate_intent» сначала ищет <a>‑ссылку и кликает (метод LINK).
#   • Если ссылка не найдена, кликает по координатам boundingRect (метод COORD).
#   • В крайнем случае — fallback driver.get() (метод DIRECT).
#   • В лог выводится, какой метод сработал: [LINK/COORD/DIRECT].
# ------------------------------------------------------------
import sys, os, json, time, datetime, random
from pathlib import Path
from html import unescape
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Set, Tuple
from fake_useragent import UserAgent
from src.config import settings

# import undetected_chromedriver as uc
import seleniumwire.undetected_chromedriver as uc
from mitmproxy import http

from selenium_stealth import stealth
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, WebDriverException,
)

from dataclasses import dataclass
from collections import defaultdict
import tempfile, zipfile


@dataclass
class TabState:
    pending_url: str | None = None  # ждём first completed
    last_nav_ts: float = 0.0  # время последнего принятого completed_navigation
    last_user_ts: float = 0.0  # последний интерактивный эвент
    last_url: str = "about:blank"


step_counter = 0
FAIL_DIR = "replay_fails"
MAX_NAV_RETRIES = 10
CAPTCHA_KEYWORDS = ["captcha", "checkcaptcha", "yandex.ru/check", "showcaptcha", "https://ya.ru/showcaptcha"]
os.makedirs(FAIL_DIR, exist_ok=True)
tabs: dict[int, TabState] = defaultdict(TabState)


def log(msg: str):
    global step_counter
    stamp = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{stamp}][{step_counter:04d}] {msg}")
    sys.stdout.flush()


# ---------- helpers --------------------------------------------------------
# разделяет куки яндекса по доменам(особенность яндекса)
def h2_blocker(flow: http.HTTPFlow):
    if flow.request.pretty_host.endswith(('yandex.ru', 'ya.ru')):
        # убираем ALPN h2 из ClientHello
        if hasattr(flow.client_conn, 'alpn_offers'):
            flow.client_conn.alpn_offers = [b'http/1.1']
        # и принудительно понижаем CONNECT-туннель
        if hasattr(flow.server_conn, 'alpn_offers'):
            flow.server_conn.alpn_offers = [b'http/1.1']


def _dup_ya_domains(ck: dict) -> list[dict]:
    d = ck.get('domain', '').lstrip('.')  # .ya.ru → ya.ru
    copies = [ck]  # всегда возвращаем исходник

    if d.endswith('ya.ru'):
        other = ck.copy();
        other['domain'] = '.yandex.ru'
        copies.append(other)
    elif d.endswith('yandex.ru'):
        other = ck.copy();
        other['domain'] = '.ya.ru'
        copies.append(other)

    # убираем точные дубли (name, domain, path)
    seen = set()
    uniq = []
    for c in copies:
        key = (c['name'], c.get('domain'), c.get('path'))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def is_captcha_url(raw_url: str) -> bool:
    """
    Проверяет, встретилось ли в hostname+path одно из ключевых слов из CAPTCHA_KEYWORDS.
    Игнорирует query-параметры и фрагменты.
    """
    p = urlparse(raw_url.lower())
    # Собираем только netloc + path, без ?query и #frag
    target = f"{p.netloc}{p.path}"
    # Проверяем каждое ключевое слово в этой строке
    return any(kw in target for kw in CAPTCHA_KEYWORDS)


def check_captcha(driver, pause_for: int = 20):
    url = driver.current_url
    if is_captcha_url(url):
        log(f"!!! CAPTCHA detected at {url}, waiting {pause_for}s for manual solve…")
        time.sleep(pause_for)
        driver.refresh()
        wait_for_dom_ready(driver)
        new_url = driver.current_url
        if is_captcha_url(new_url):
            log(f"!!! CAPTCHA still present at {new_url}, aborting")
            try:
                driver.quit()
            except:
                pass
            raise RuntimeError("CAPTCHA page still detected after manual wait")
        else:
            log(f"+++ CAPTCHA bypassed, continuing on {new_url}")
            for d in ['ya.ru', '.ya.ru', 'yandex.ru', '.yandex.ru']:
                try:
                    # Selenium удаляет только для «текущего» домена,
                    # поэтому сначала меняем location на нужный домен,
                    # а потом вызываем delete_cookie.
                    driver.get(f"https://{d.lstrip('.')}/favicon.ico")  # лёгкий «пинг» без редиректа
                    driver.delete_cookie('spravka')
                except Exception:
                    pass
            return True
    return False


def merge_cookies(old_cookies: list[dict], new_cookies: list[dict]) -> list[dict]:
    """
    Объединяет два списка куков, используя тройку (name, domain, path) как уникальный ключ.
    При совпадении ключа вновь сохраняется полный словарь из new_cookies.
    """
    merged: dict[tuple, dict] = {}

    # 1) Сначала кладём все "старые" куки целиком
    for ck in old_cookies:
        key = (ck["name"], ck.get("domain"), ck.get("path"))
        merged[key] = ck

    # 2) Затем накатываем "новые" — они полностью перезапишут старые записи по тому же ключу
    for ck in new_cookies:
        key = (ck["name"], ck.get("domain"), ck.get("path"))
        merged[key] = ck

    # 3) Возвращаем список всех полных dict‑ов
    return list(merged.values())


def pick_chrome_ua() -> str:
    # 1) Поднимаем «чистый» драйвер, чтобы узнать версию
    opts = uc.ChromeOptions()
    temp_driver = uc.Chrome(options=opts)
    caps = temp_driver.capabilities
    log(f"[DEBUG] CAPS:{caps}")
    version = caps.get("browserVersion") or caps.get("version") or ""
    temp_driver.quit()

    if not version:
        return settings.DEFAULT_UA

    major = version.split(".", 1)[0]

    # 2) Пытаемся достать из fake_useragent.data список Chrome-UA
    try:
        ua = UserAgent()
        data = ua.data
        if isinstance(data, dict):
            chrome_list = data.get("browsers", {}).get("chrome", [])
            # фильтруем по нашему major
            candidates = [u for u in chrome_list if f"Chrome/{major}." in u]
            if candidates:
                return random.choice(candidates)

        # Если data не dict или нет подходящих — пробуем ua.chrome
        fallback = ua.chrome
        if f"Chrome/{major}." in fallback:
            return fallback

    except Exception:
        # любая ошибка — падаем на дефолт
        pass

    return settings.DEFAULT_UA


def normalize_href(href: str, base: str) -> str:
    href = unescape(href or "")
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{href}"
    return href


def wait_for_dom_ready(driver, timeout: int = 10):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        log("[WARN] DOM readyState timeout")


def switch_to_frame_chain(driver, chain: List[int]):
    driver.switch_to.default_content()
    for idx in chain:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
        if idx < len(frames):
            driver.switch_to.frame(frames[idx])
        else:
            raise NoSuchElementException(f"iframe index {idx} not found")


def enter_shadow_path(ctx, shadow_path: List[str]):
    for sel in shadow_path:
        host = ctx.find_element(By.CSS_SELECTOR, sel)
        ctx = host.shadow_root if host.shadow_root else host.parent.execute_script("return arguments[0].shadowRoot",
                                                                                   host)
    return ctx


def build_combined_selector(data: Dict[str, Any]) -> Optional[str]:
    parts, tag = [], data.get("tag")
    if tag:
        parts.append(tag)
    if data.get("id"):
        return tag + f"#{data['id']}"
    parts.extend("." + cls for cls in data.get("classList", []))
    if data.get("name"):
        parts.append(f"[name='{data['name']}']")
    if data.get("placeholder"):
        parts.append(f"[placeholder='{data['placeholder']}']")
    if data.get("type"):
        parts.append(f"[type='{data['type']}']")
    return "".join(parts) or None


def find_in_context(ctx, data: Dict[str, Any]):
    sel = data.get("selector")
    if sel:
        try:
            return ctx.find_element(By.CSS_SELECTOR, sel)
        except Exception:
            pass
    aria = data.get("aria") or {}
    if aria.get("label"):
        try:
            return ctx.find_element(By.CSS_SELECTOR, f"[aria-label='{aria['label']}']")
        except Exception:
            pass
    if aria.get("role"):
        try:
            return ctx.find_element(By.CSS_SELECTOR, f"[role='{aria['role']}']")
        except Exception:
            pass
    for key, by in (("id", By.ID), ("name", By.NAME)):
        if data.get(key):
            try:
                return ctx.find_element(by, data[key])
            except Exception:
                pass
    combo = build_combined_selector(data)
    if combo:
        try:
            return ctx.find_element(By.CSS_SELECTOR, combo)
        except Exception:
            pass
    return None


def resolve_element(driver, data: dict, timeout: float = 2.0):
    chain = data.get("frameChain", [])
    shadow = data.get("shadowPath", [])
    href = data.get("href")
    snippet = (data.get("text") or "").strip()[:80]

    def _attempt(ctx):
        # 1) оригинальный селектор / aria
        el = find_in_context(ctx, data)
        if el:
            return el

        # 2) <a href="...">
        if href:
            q = f'a[href="{href}"],a[href="{href.rstrip("/")}" ]'
            el = ctx.find_elements(By.CSS_SELECTOR, q)
            if el:
                return el[0]

            netloc = urlparse(href).netloc
            el = ctx.find_elements(By.CSS_SELECTOR, f'a[href*="{netloc}"]')
            if el:
                return el[0]

        # 3) совпадение по тексту
        if snippet:
            for a in ctx.find_elements(By.TAG_NAME, "a"):
                if snippet.lower() in (a.text or "").lower():
                    return a
        return None

    def _find(_):
        try:
            switch_to_frame_chain(driver, chain)
            ctx = driver if not shadow else enter_shadow_path(driver, shadow)
            return _attempt(ctx)
        except Exception:
            return None

    try:
        return WebDriverWait(driver, timeout).until(_find)
    except TimeoutException:
        return None


# def resolve_element(driver, data: Dict[str, Any], timeout: float = 4):
#     def resolve_element(driver, data: Dict[str, Any], timeout: float = 2.0):
#         """
#         Пытаемся найти элемент тремя волнами:
#         1.  исходный selector или aria‑атрибуты (как было);
#         2.  <a> с тем же href;
#         3.  <a> по фрагменту текста.
#         """
#         chain = data.get("frameChain", [])
#         shadow = data.get("shadowPath", [])
#         href = data.get("href")
#         snippet = (data.get("text") or "").strip()[:80]  # первая строка подсказки
#
#         def _attempt(ctx):
#             # 1) оригинальный путь
#             el = find_in_context(ctx, data)
#             if el:
#                 return el
#
#             # 2) <a href="…">  (точный или c «/» на конце)
#             if href:
#                 el = ctx.find_elements(By.CSS_SELECTOR, f'a[href="{href}"],a[href="{href.rstrip("/")}"]')
#                 if el:
#                     return el[0]
#
#                 # подстраховка: домен + начало пути
#                 netloc = urlparse(href).netloc
#                 el = ctx.find_elements(By.CSS_SELECTOR, f'a[href*="{netloc}"]')
#                 if el:
#                     return el[0]
#
#             # 3) текстовый фрагмент (упрощённый вариант, без XPath)
#             if snippet:
#                 for a in ctx.find_elements(By.TAG_NAME, "a"):
#                     if snippet.lower() in (a.text or "").lower():
#                         return a
#
#             return None
#
#         def _find(_driver):
#             try:
#                 switch_to_frame_chain(_driver, chain)
#                 ctx = _driver if not shadow else enter_shadow_path(_driver, shadow)
#                 return _attempt(ctx)
#             except Exception:
#                 return None
#
#         try:
#             return WebDriverWait(driver, timeout).until(_find)
#         except TimeoutException:
#             return None
#     # chain = data.get("frameChain", [])
#     # shadow = data.get("shadowPath", [])
#     #
#     # def _find(_):
#     #     try:
#     #         switch_to_frame_chain(driver, chain)
#     #         ctx = driver if not shadow else enter_shadow_path(driver, shadow)
#     #         return find_in_context(ctx, data)
#     #     except Exception:
#     #         return None
#     # try:
#     #     return WebDriverWait(driver, timeout).until(_find)
#     # except TimeoutException:
#     #     return None


def perform_click(driver, x: int, y: int):
    ActionChains(driver).move_by_offset(0, 0).move_by_offset(x, y).click().perform()


def perform_drag(driver, seq: List[Dict[str, int]], pointer_type: str = "mouse"):
    if not seq:
        return
    actions = ActionBuilder(driver)
    mouse = PointerInput(PointerInput.MOUSE if pointer_type == "mouse" else PointerInput.PEN, "drag")
    actions.add_action(mouse)
    start = seq[0]
    mouse.move_to_location(start["x"], start["y"])
    mouse.pointer_down()
    for pt in seq[1:]:
        mouse.move_to_location(pt["x"], pt["y"], origin="pointer")
    mouse.pointer_up()
    actions.perform()


def safe_hover(driver, el, data) -> bool:
    if el and el.is_displayed() and el.size["width"] and el.size["height"]:
        ActionChains(driver).move_to_element(el).perform()
        return True
    bbox = data.get("boundingRect", {})
    x = bbox.get("x", data.get("x", 0)) + (bbox.get("w", 1) // 2)
    y = bbox.get("y", data.get("y", 0)) + (bbox.get("h", 1) // 2)
    try:
        perform_click(driver, x, y)
        return True
    except WebDriverException:
        return False


def cookie_killer(drv):
    try:
        # 1) Кликаем по «принять всё» / «allow all» и подобным
        drv.execute_script("""
            [...document.querySelectorAll('[role="button"],button,div')]
              .flatMap(el => [...el.querySelectorAll('*'), el])
              .filter(el => /allow all|accept|принять|разрешить|согласен/i.test(el.textContent))
              .forEach(el => el.click());
        """)
        # 2) Кликаем по «Нет, спасибо»
        drv.execute_script("""
                   [...document.querySelectorAll('button, [role="button"], div')].forEach(el => {
                     if (/нет[,\\s]*спасибо/i.test(el.textContent)) {
                       el.click();
                     }
                   });
               """)
    except Exception:
        pass


# ---------- core replay ----------------------------------------------------

def replay_events(
        events: List[Dict[str, Any]],
        skip_substrings: Optional[Set[str]] = None,
        user_agent: Optional[str] = None,
        cookies: Optional[List[Dict[str, Any]]] = None,
        proxy: Optional[str] = None) -> Tuple[list[Dict[str, Any]], str]:
    global step_counter
    all_cookies = cookies or []
    last_kill = time.time()
    skip_substrings = skip_substrings or set()

    events.sort(key=lambda e: e.get("timestamp",
                                    0))  # сортируем JSON инструкцию по timestamp чтобы реплей работал более стабильно и последовательно

    first_url: Dict[int, str] = {}
    for ev in events:
        raw = ev.get("data")
        if isinstance(raw, dict):
            u = raw.get("url") or raw.get("href")
            if u and not u.startswith(("chrome:", "about:")):
                first_url.setdefault(ev.get("tabId"), u)

    opts = uc.ChromeOptions()

    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-insecure-localhost")

    need_random = proxy is not None  # рандомим только если выходим через прокси(пока заглушка на FALSE)

    if not user_agent:
        if need_random:
            try:
                user_agent = pick_chrome_ua()
                log(f"[INFO] Randomly selected User-Agent: {user_agent}")
            except Exception as e:
                log(f"[Log ERROR] {e}")  # На всякий случай подставляем дефолтный user-agent, чтобы не упасть
                # user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                #               "Chrome/115.0.0.0 Safari/537.36")
                user_agent = settings.DEFAULT_UA
                log(f"[INFO] Default User-Agent from .env selected: {user_agent}")
        else:
            user_agent = settings.DEFAULT_UA
            log(f"[INFO] Default User-Agent from .env selected: {user_agent}")

    seleniumwire_opts = {}

    if proxy:

        sw_port = 9000 + (os.getpid() % 1000)
        p = urlparse(proxy)  # proxy = "http://user:pass@host:port"
        if "://" not in proxy:
            proxy = "http://" + proxy
            creds = ""
            p = urlparse(proxy)

        if p.username and p.password:
            creds = f"{p.username}:{p.password}@"
            # HTTP и HTTPS прокси с авторизацией

            seleniumwire_opts = {
                'disable_http2': ['.yandex.ru', '.ya.ru'],
                'port': sw_port,
                'proxy': {
                    'http': f"http://{creds}{p.hostname}:{p.port}",
                    'https': f"http://{creds}{p.hostname}:{p.port}",

                },
                'exclude_hosts': ['localhost', '127.0.0.1'],
                'mitmproxy_opts': {
                    'http2': False
                },
                'mitmproxy_addons': [h2_blocker],
            }
        else:
            seleniumwire_opts = {
                'disable_http2': ['.yandex.ru', '.ya.ru'],
                'port': sw_port,
                'proxy': {
                    'http': f"http://{p.hostname}:{p.port}",
                    'https': f"http://{p.hostname}:{p.port}",
                },
                'exclude_hosts': ['localhost', '127.0.0.1'],
                'mitmproxy_opts': {
                    'http2': False
                },
                'mitmproxy_addons': [h2_blocker],
            }

    opts.add_argument(f"--user-agent={user_agent}")

    opts.add_argument("--disable-http2")

    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!SW OPTS:", seleniumwire_opts)
    driver = uc.Chrome(options=opts,
                       seleniumwire_options=seleniumwire_opts
                       )

    # ————— STEALTH.PY INTEGRATION —————
    stealth(
        driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )
    # ————— END STEALTH.PY —————

    # ————— STEALTH PATCH START —————
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {
        "headers": {"Accept-Language": "en-US,en;q=0.9"}
    })
    driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {
        "timezoneId": "Europe/Moscow"
    })
    driver.execute_cdp_cmd("Emulation.setNavigatorOverrides", {
        "platform": "Win32",
        "hardwareConcurrency": random.choice([4, 8, 12]),
        "deviceMemory": random.choice([4, 8, 16])
    })
    # ————— STEALTH PATCH END —————

    driver.get("https://api.ipify.org?format=json")  # для теста прокси

    for entry in driver.get_log("browser"):
        print("[BROWSER LOG]", entry)

    if cookies:
        # выбираем URL для инициализации (первый попавшийся)
        init_url = next(iter(first_url.values()), None)
        if init_url:
            driver.get(init_url)
            wait_for_dom_ready(driver)

            host = urlparse(init_url).hostname or ""
            # отбираем только те куки, чей domain совпадает с текущим хостом
            init_cookies = [
                ck for ck in cookies
                # lstrip('.') — чтобы совпадать и ".ya.ru" и "ya.ru"
                if host.endswith(ck.get("domain", "").lstrip("."))
            ]
            for ck in init_cookies:
                try:
                    driver.add_cookie(ck)
                    log(f"[INIT-COOKIE] added {ck['name']} for {ck['domain']}")
                except Exception as e:
                    log(f"[WARN] init cookie {ck['name']} failed: {e}")

    handles, prev_input = {}, None

    for ev in events:
        check_captcha(driver, pause_for=30)
        if time.time() - last_kill >= 5:
            cookie_killer(driver)
            last_kill = time.time()

        step_counter += 1
        typ = ev.get("type", "").lower()
        if any(sub in typ for sub in skip_substrings):
            log(f"{typ:>12s} (skipped)")
            continue

        log(f"{typ:>12s} Δ={ev.get('delta', 0):>4} ms")
        # time.sleep(min(ev.get("delta", 150) / 1000, 0.5))
        base = ev.get("delta", 150) / 1000
        sleep = max(0.015, base + random.uniform(-0.4, 0.6) * base)
        time.sleep(min(sleep, 1.2))

        tab = ev.get("tabId")
        if tab is None:
            continue

        if tab not in handles:
            driver.switch_to.new_window("tab")
            handles[tab] = driver.current_window_handle
            url0 = first_url.get(tab)
            if url0:
                driver.get(url0)
                wait_for_dom_ready(driver)
                time.sleep(1.5)
                st = tabs[tab]
                now = time.time()
                st.last_user_ts = st.last_nav_ts = now
                st.last_url = url0

        driver.switch_to.window(handles[tab])
        data = ev.get("data", {}) or {}

        try:
            # обновляем последний интерактивный таймштамп
            if typ in {"click", "wheel", "scroll", "keydown", "input", "drag_sequence", "form_submit"}:
                tabs[tab].last_user_ts = time.time()

            # NAVIGATION ------------------------------------------------
            # … внутри цикла по событиям …
            if typ == "navigate_intent":
                href = data.get("href")
                if not href or not data.get("was_recent_click"):
                    continue

                st = tabs[tab]
                href_full = normalize_href(href, st.last_url)

                # дублирующий переход — пропускаем
                if st.pending_url == href_full:
                    log(f"    >>> duplicate NAV intent to '{href_full}', skipped")
                    continue

                st.pending_url = href_full

                # отбираем куки для этого домена
                target_host = urlparse(href_full).hostname or ""
                relevant_cookies = [
                    ck for ck in all_cookies
                    if target_host.endswith(ck.get("domain", "").lstrip("."))
                ]

                for attempt in range(1, MAX_NAV_RETRIES + 1):
                    method = "DIRECT"

                    existed = {(c['name'], c.get('domain'), c.get('path')) for c in driver.get_cookies()}

                    # --- подгружаем куки для этого хоста ---
                    for ck in relevant_cookies:
                        key = (ck['name'], ck.get('domain'), ck.get('path'))
                        if key not in existed:
                            try:
                                driver.add_cookie(ck)
                                log(f"[COOKIE] added '{ck['name']}' → domain={ck['domain']}")
                            except Exception as e:
                                log(f"[WARN] failed to add cookie {ck.get('name')} for domain {ck.get('domain')}: {e}")

                    # 1) Пытаемся кликнуть по ссылке
                    el = resolve_element(driver, data, timeout=0.6)
                    if el:
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
                            ActionChains(driver).move_to_element(el).click().perform()
                            method = "LINK"
                        except Exception:
                            el = None

                    # 2) Фоллбэк — координатный клик
                    if not el and data.get("boundingRect"):
                        bbox = data["boundingRect"]
                        try:
                            perform_click(driver, bbox["x"] + 3, bbox["y"] + 3)
                            method = "COORD"
                        except Exception:
                            pass

                    # 3) Прямой GET
                    if method == "DIRECT":
                        driver.get(href_full)

                    wait_for_dom_ready(driver)
                    current = driver.current_url
                    log(f"    >>> NAV via {method}, landed on {current}")

                    # проверяем капчу лишь по ключевым словам
                    check_captcha(driver, pause_for=20)
                    # lc = current.lower()
                    # if any(kw in lc for kw in CAPTCHA_KEYWORDS):
                    #     if attempt < MAX_NAV_RETRIES:
                    #         log(f"    !!! Detected captcha on attempt {attempt}, retrying…")
                    #         driver.delete_all_cookies()
                    #         driver.get("about:blank")
                    #         time.sleep(1)
                    #         continue
                    #     else:
                    #         raise RuntimeError(f"Captcha persisted after {MAX_NAV_RETRIES} attempts")

                    # любой успешный (не-кэпча) переход засчитываем и выходим из цикла
                    st.last_url = current
                    st.pending_url = None
                    st.last_nav_ts = time.time()
                    break

                new_ck = sum((_dup_ya_domains(c) for c in driver.get_cookies()), [])
                all_cookies = merge_cookies(all_cookies, new_ck)
                continue

            # COMPLETED NAVIGATION --------------------------------------
            if typ == "completed_navigation":
                now = time.time()
                url_now = data.get("url", driver.current_url)
                st = tabs[tab]

                # если ожидали именно этот URL — сбрасываем pending и принимаем
                if st.pending_url and url_now.startswith(st.pending_url):
                    st.pending_url = None
                    st.last_url = url_now
                    st.last_nav_ts = now
                    log(f"    >>> NAV accepted (pending): {url_now}")
                else:
                    # fallback: по таймингу старые переходы
                    accept = (now - st.last_user_ts >= 0.15 and now - st.last_nav_ts >= 0.30)
                    if accept:
                        st.last_url = url_now
                        st.last_nav_ts = now
                        log(f"    >>> NAV accepted: {url_now}")
                    else:
                        log(f"    !!! NAV ignored:  {url_now}")
                continue

            # ACTIONS ------------------------------------------------------
            if typ == "click":
                el = resolve_element(driver, data)
                if el and el.is_enabled():
                    try:
                        el.click()
                    except Exception:
                        bbox = data.get("boundingRect", {})
                        perform_click(driver, bbox.get("x", 0), bbox.get("y", 0))
                else:
                    bbox = data.get("boundingRect", {})
                    perform_click(driver, bbox.get("x", 0), bbox.get("y", 0))

            elif typ == "keydown":
                act = driver.switch_to.active_element
                raw_key = data.get("key", "")
                # если это «Enter», мапим на настоящий Keys.ENTER
                if raw_key.lower() == "enter":
                    selenium_key = Keys.ENTER
                elif raw_key.lower() in ("meta", "command"):
                    selenium_key = Keys.META  # или Keys.COMMAND
                else:
                    # для любых остальных клавиш — передаём 그대로 строку
                    selenium_key = raw_key

                # собираем модификаторы
                mods = [
                    k for k, flag in [
                        (Keys.CONTROL, "ctrlKey"),
                        (Keys.SHIFT, "shiftKey"),
                        (Keys.ALT, "altKey"),
                        (Keys.COMMAND, "metaKey"),
                        (Keys.META, "metaKey"),
                    ] if data.get(flag)
                ]
                # наконец — шлём все вместе
                act.send_keys(*mods, selenium_key)

            elif typ == "input":
                el = resolve_element(driver, data)
                if el:
                    sel = data.get("selector") or build_combined_selector(data) or ""
                    val = data.get("value", "")
                    cur = el.get_attribute("value") or ""
                    if prev_input == sel and val.startswith(cur):
                        el.send_keys(val[len(cur):])
                    else:
                        el.clear();
                        el.send_keys(val)
                    prev_input = sel

            elif typ == "scroll":
                driver.execute_script("window.scrollTo(arguments[0], arguments[1]);", data.get("x", 0),
                                      data.get("y", 0))

            elif typ == "wheel":
                driver.execute_script("window.scrollBy(0, arguments[0]);", data.get("deltaY", data.get("y", 0)))
                time.sleep(random.uniform(0.1, 0.2))

            elif typ == "mouse_move":
                pts = data.get("positions", [])
                if pts:
                    perform_drag(driver, pts, data.get("pointerType", "mouse"))
                time.sleep(random.uniform(0.05, 0.15))

            elif typ == "hover":
                el = resolve_element(driver, data, timeout=0.5)
                if not safe_hover(driver, el, data):
                    log("hover skipped")

            elif typ == "drag_sequence":
                perform_drag(driver, data.get("points", []), data.get("pointerType", "mouse"))

        except WebDriverException as e:
            log(f"ERROR during {typ}: {e}")
            try:
                driver.save_screenshot(os.path.join(FAIL_DIR, f"fail_{step_counter:04d}.png"))
            except Exception:
                pass
            finally:
                raise

    final_cookies = all_cookies
    # final_cookies = driver.get_cookies()
    final_user_agent = driver.execute_script("return navigator.userAgent;")
    driver.quit()
    return final_cookies, final_user_agent


# ---------- CLI -----------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Replay browser session (fast mode)")
    parser.add_argument("--log", default="sorted_log.json", help="Path to events JSON")
    parser.add_argument("--skip", default="", help="Comma-separated substrings to skip")
    parser.add_argument("--user-agent", dest="ua", help="User-Agent string to set on the browser")
    parser.add_argument("--cookies", help="Path to JSON file with cookies")
    parser.add_argument("--proxy", help="Proxy URL, e.g. http://user:pass@host:port")
    args = parser.parse_args()

    skip_set = {s.strip().lower() for s in args.skip.split(",") if s.strip()}

    cookies_list: List[Dict[str, Any]] = []
    if args.cookies:
        try:
            with open(args.cookies, "r", encoding="utf-8") as f:
                cookies_list = json.load(f)
        except Exception as e:
            log(f"[Cookie ERROR] {e}");
            sys.exit(1)

    try:
        with open(args.log, "r", encoding="utf-8") as f:
            evs = json.load(f)
    except Exception as e:
        log(f"[Log ERROR] {e}");
        sys.exit(1)

    ua_str = args.ua or None
    if not ua_str:
        try:
            ua_str = UserAgent().random
            log(f"[INFO] Random UA: {ua_str}")
        except Exception as e:
            log(f"[UA ERROR] {e}")
            ua_str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"

    evs.sort(key=lambda e: e.get("timestamp", 0))

    replay_events(
        events=evs,
        skip_substrings=skip_set,
        user_agent=ua_str,
        cookies=cookies_list,
        proxy=args.proxy,
    )
