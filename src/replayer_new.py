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
SPEED = 1  # >1 – ускорить в 1.8×; <1 – замедлить
import sys, os, json, time, datetime, random
from pathlib import Path
from html import unescape
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Set, Tuple
from fake_useragent import UserAgent
from src.config import settings

import undetected_chromedriver as uc

# from selenium_stealth import stealth
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, WebDriverException, MoveTargetOutOfBoundsException,
)

from dataclasses import dataclass
from collections import defaultdict
import socket, atexit, subprocess


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


def _find_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------- helpers --------------------------------------------------------

# разделяет куки яндекса по доменам(особенность яндекса)
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


def check_captcha(driver, pause_for: int = 60, poll_interval: int = 2) -> bool:
    """
    Если на странице капча, ждем её ручного решения до pause_for секунд,
    каждые poll_interval секунд проверяя, не ушла ли капча.
    Возвращает True, если капча была и успешно обойдена, False если не было капчи.
    В случае, если капча осталась по истечении таймаута — кидает RuntimeError.
    """
    # проверяем сразу
    try:
        current = driver.current_url
    except WebDriverException:
        return False

    if not is_captcha_url(current):
        return False

    log(f"!!! CAPTCHA detected at {current}, waiting up to {pause_for}s for manual solve…")
    start = time.time()

    # ждем решения: каждые poll_interval секунд проверяем URL
    while time.time() - start < pause_for:
        time.sleep(poll_interval)
        try:
            new_url = driver.current_url
        except WebDriverException:
            # если окно закрылось — выходим
            break
        if not is_captcha_url(new_url):
            log(f"+++ CAPTCHA seems solved after {int(time.time() - start)}s, refreshing…")
            break
        log(f"    still captcha at {new_url}, waited {int(time.time() - start)}s…")
    else:
        # таймаут
        log(f"!!! CAPTCHA still present after {pause_for}s, aborting")
        try:
            driver.quit()
        except Exception:
            pass
        raise RuntimeError("CAPTCHA page still detected after timeout")

    # обновляем страницу и качаем куки
    driver.refresh()
    wait_for_dom_ready(driver)
    final = driver.current_url
    if is_captcha_url(final):
        log(f"!!! CAPTCHA reappeared at {final}, aborting")
        try:
            driver.quit()
        except Exception:
            pass
        raise RuntimeError("CAPTCHA page still detected after refresh")

    log(f"+++ CAPTCHA bypassed, continuing on {final}")
    try:
        time.sleep(0.3)
        driver.delete_cookie('spravka')
    except Exception:
        pass

    return True


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

    kind = "mouse" if pointer_type == "mouse" else "pen"
    pointer = PointerInput(kind=kind, name="drag")
    actions = ActionBuilder(driver, pointer)

    # Начальная точка
    start = seq[0]
    actions.pointer_action.move_to_location(start["x"], start["y"])
    actions.pointer_action.pointer_down()

    # Перемещение по точкам
    for pt in seq[1:]:
        actions.pointer_action.move_to_location(pt["x"], pt["y"])
        actions.pointer_action.pause(0.01)  # добавим небольшую паузу для реалистичности

    actions.pointer_action.pointer_up()
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


def synthetic_hover(driver, el, data):
    """
    Человечный hover, привязанный к лог-дельте:
      - движение по 3–6 шагам,
      - одно микроколебание,
      - остаток — dwell.
    """
    rect = el.rect
    tx = rect['x'] + rect['width'] / 2
    ty = rect['y'] + rect['height'] / 2

    # общее время hover из лога (ms → s)
    total = data.get('delta', 50) / 1000.0

    # разбиваем это время:
    # 50–60% идёт на движение, 5–10% на jitter, остальное — dwell
    move_frac = random.uniform(0.5, 0.6)
    jitter_frac = random.uniform(0.05, 0.1)
    movement_time = total * move_frac
    jitter_time = total * jitter_frac
    dwell_time = max(0.0, total - movement_time - jitter_time)

    # шаги движения: 3–6 точек
    steps = random.randint(3, 6)
    pause_per_step = movement_time / steps

    # создаём action-builder
    mouse = PointerInput(kind="mouse", name="mouse")
    actions = ActionBuilder(driver, mouse)

    # 1) основное движение к центру
    for i in range(steps):
        t = (i + 1) / steps
        x = tx * t + random.gauss(0, 1)  # шум ±1px
        y = ty * t + random.gauss(0, 1)
        actions.pointer_action.move_to_location(int(x), int(y))
        actions.pointer_action.pause(pause_per_step)
    try:
        actions.perform()
    except MoveTargetOutOfBoundsException:
        # если цель не в зоне — просто пропускаем
        pass

    # 2) одно микроколебание вокруг цели
    ox = int(tx + random.uniform(-2, 2))
    oy = int(ty + random.uniform(-2, 2))
    micro = ActionBuilder(driver, mouse)
    micro.pointer_action.move_to_location(ox, oy)
    micro.pointer_action.pause(jitter_time)
    try:
        micro.perform()
    except MoveTargetOutOfBoundsException:
        pass

    # 3) dwell
    time.sleep(dwell_time)


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

    # -------------------Part of stealth patch------------------------------------------
    # opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    # opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-insecure-localhost")
    # --------------------------------------------------------------------------------

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

    # seleniumwire_opts = {}
    #
    # if proxy:
    #
    #     sw_port = 9000 + (os.getpid() % 1000)
    #     p = urlparse(proxy)  # proxy = "http://user:pass@host:port"
    #     if "://" not in proxy:
    #         proxy = "http://" + proxy
    #         creds = ""
    #         p = urlparse(proxy)
    #
    #     if p.username and p.password:
    #         creds = f"{p.username}:{p.password}@"
    #         # HTTP и HTTPS прокси с авторизацией
    #
    #         seleniumwire_opts = {
    #             # 'disable_http2': ['.yandex.ru', '.ya.ru'],
    #             'port': sw_port,
    #             'proxy': {
    #                 'http': f"http://{creds}{p.hostname}:{p.port}",
    #                 'https': f"http://{creds}{p.hostname}:{p.port}",
    #
    #             },
    #             'exclude_hosts': ['localhost', '127.0.0.1'],
    #             # 'mitmproxy_opts': {
    #             #     'http2': False
    #             # },
    #             # 'mitmproxy_addons': [h2_blocker],
    #         }
    #     else:
    #         seleniumwire_opts = {
    #             # 'disable_http2': ['.yandex.ru', '.ya.ru'],
    #             'port': sw_port,
    #             'proxy': {
    #                 'http': f"http://{p.hostname}:{p.port}",
    #                 'https': f"http://{p.hostname}:{p.port}",
    #             },
    #             'exclude_hosts': ['localhost', '127.0.0.1'],
    #             # 'mitmproxy_opts': {
    #             #     'http2': False
    #             # },
    #             # 'mitmproxy_addons': [h2_blocker],
    #         }

    if proxy:
        print(proxy)
        # proxy == "http://127.0.0.1:<port>" после поднятого proxy.py
        opts.add_argument(
            "--proxy-server="
            f"http={proxy};https={proxy}"
        )
    opts.add_argument(f"--user-agent={user_agent}")

    # opts.add_argument("--disable-http2")

    # print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!SW OPTS:", seleniumwire_opts)

    driver = uc.Chrome(options=opts)

    # ————— STEALTH.PY INTEGRATION —————
    # stealth(
    #     driver,
    #     languages=["en-US", "en"],
    #     vendor="Google Inc.",
    #     platform="Win32",
    #     webgl_vendor="Intel Inc.",
    #     renderer="Intel Iris OpenGL Engine",
    #     fix_hairline=True,
    # )
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
    STEALTH_JS = r"""
    // 1) navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => false });

    // 2) window.chrome
    window.chrome = {
      runtime: {},
      // можно добавить другие методы/проперти, которые проверяют сайты
    };

    // 3) Permissions API
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.__proto__.query = parameters =>
      parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(parameters);

    // 4) Plugins & MimeTypes
    Object.defineProperty(navigator, 'plugins', {
      get: () => [ { name: 'Chrome PDF Plugin' }, { name: 'Chrome PDF Viewer' }, { name: 'Native Client' } ]
    });
    Object.defineProperty(navigator, 'mimeTypes', {
      get: () => [ { type: 'application/pdf' } ]
    });

    // 5) languages
    Object.defineProperty(navigator, 'languages', {
      get: () => ['en-US', 'en']
    });

    // 6) WebGL vendor/renderer spoof
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
      // 37445 = UNMASKED_VENDOR_WEBGL, 37446 = UNMASKED_RENDERER_WEBGL
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel Iris OpenGL Engine';
      return getParameter.call(this, parameter);
    };

    // 7) userAgent override (если нужно ещё подменить в рантайме)
    Object.defineProperty(navigator, 'userAgent', {
      get: () => window.__originalUA || navigator.userAgent
    });
    
    // Canvas
const toDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function() {
  this.getContext('2d').fillText(' ', 0, 0);
  return toDataURL.apply(this, arguments);
};
// AudioContext
const origCreateAnalyser = AudioContext.prototype.createAnalyser;
AudioContext.prototype.createAnalyser = function() {
  const analyser = origCreateAnalyser.apply(this, arguments);
  const origGetFloatFrequencyData = analyser.getFloatFrequencyData;
  analyser.getFloatFrequencyData = function() {
    arguments[0] = arguments[0].map(v => v + (Math.random() * 0.0001));
    return origGetFloatFrequencyData.apply(this, arguments);
  };
  return analyser;
};

Object.defineProperty(navigator, 'connection', {
  get: () => ({ effectiveType: '4g', downlink: 10, rtt: 50 })
});

    const origRTCPeer = window.RTCPeerConnection;
window.RTCPeerConnection = function(config) {
  const pc = new origRTCPeer(config);
  const origCreateOffer = pc.createOffer;
  pc.createOffer = function() {
    return origCreateOffer.call(this).then(offer => {
      return new RTCSessionDescription({
        type: offer.type,
        sdp: offer.sdp.replace(/a=candidate:.+\r\n/g, '')
      });
    });
  };
  return pc;
};
delete window.console.debug;

const origFunctionToString = Function.prototype.toString;
Function.prototype.toString = function() {
  if (this === navigator.permissions.query) {
    return 'function query() { [native code] }';
  }
  return origFunctionToString.call(this);
};
    
    """
    orig_ua = driver.execute_cdp_cmd('Browser.getVersion', {})['userAgent']
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': f"window.__originalUA = '{orig_ua}';\n{STEALTH_JS}"
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
        check_captcha(driver, pause_for=60)
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
            if typ in {"click", "wheel", "scroll", "keydown", "input",
                       "drag_sequence", "form_submit", "hover", "hover_generic"}:
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
                    check_captcha(driver, pause_for=60)
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
                # 1) Находим элемент и фокусируем на нём
                el = resolve_element(driver, data, timeout=0.5)
                if el:
                    try:
                        el.click()
                    except:
                        driver.execute_script("arguments[0].focus()", el)
                else:
                    el = driver.switch_to.active_element
                raw_key = data.get("key", "")
                # 2) Мэппинг спецклавиш из лога в реальные selenium Keys или символы
                special = {
                    "Backspace": Keys.BACKSPACE,
                    "Enter": Keys.ENTER,
                    "Tab": Keys.TAB,
                    " ": " ",
                    "Spacebar": " ",
                    # при необходимости можно докинуть ещё: "Escape": Keys.ESCAPE, и т. д.
                }
                # 3) Выбираем, что именно шлём: либо спецклавишу, либо одиночный символ
                if raw_key in special:
                    selenium_key = special[raw_key]
                elif len(raw_key) == 1:
                    selenium_key = raw_key
                else:
                    # непривычный key, просто игнорируем
                    continue
                # 4) Собираем модификаторы (Ctrl, Shift и т.п.) из data и шлём всё вместе
                mods = [
                    k for k, flag in [
                        (Keys.CONTROL, "ctrlKey"),
                        (Keys.SHIFT, "shiftKey"),
                        (Keys.ALT, "altKey"),
                        (Keys.COMMAND, "metaKey"),
                        (Keys.META, "metaKey"),
                    ] if data.get(flag)
                ]
                el.send_keys(*mods, selenium_key)
                # 5) Пауза, чтобы выдержать timing из лога
                time.sleep(max(0.01, data.get("delta", 50) / 1000))


            elif typ == "scroll":
                # целевые координаты из лога
                target_x = data.get("x", 0)
                target_y = data.get("y", 0)

                # получаем текущие позиции прокрутки
                current_x = driver.execute_script("return window.scrollX")
                current_y = driver.execute_script("return window.scrollY")

                # рандомное число шагов
                steps = random.randint(5, 8)
                dx = (target_x - current_x) / steps
                dy = (target_y - current_y) / steps

                # плавный скролл
                for i in range(steps):
                    driver.execute_script(
                        "window.scrollBy(arguments[0], arguments[1]);",
                        dx, dy
                    )
                    time.sleep(random.uniform(0.05, 0.2))

                # небольшой «отскок» назад-вперёд
                if random.random() < 0.3:
                    driver.execute_script("window.scrollBy(arguments[0], arguments[1]);", -dx / 3, -dy / 3)
                    time.sleep(0.1)
                    driver.execute_script("window.scrollBy(arguments[0], arguments[1]);", dx / 3, dy / 3)




            elif typ == "wheel":

                total = data.get("deltaY", data.get("y", 0))
                log_dt = data.get("delta", abs(total)) / 1000.0
                # число шагов пропорционально total, но не меньше 1 и не больше 6
                base = max(1, min(5, int(abs(total) / 100)))
                parts = random.randint(max(1, base - 1), base + 1)
                vw = driver.execute_script("return window.innerWidth")
                vh = driver.execute_script("return window.innerHeight")
                moved = 0.0

                for _ in range(parts):
                    portion = total / parts
                    dy = portion + random.uniform(-abs(portion) * 0.3, abs(portion) * 0.3)
                    moved += dy
                    if random.random() < 0.3:
                        # CDP wheel из случайной точки
                        x = random.randint(50, vw - 50)
                        y = random.randint(50, vh - 50)
                        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                            "type": "mouseWheel", "x": x, "y": y,
                            "deltaX": 0, "deltaY": dy, "pointerType": "mouse"
                        })
                    else:
                        driver.execute_script("window.scrollBy(0, arguments[0])", dy)
                    # микроколебание курсора от центра
                    if random.random() < 0.4:
                        offset_x = random.randint(-5, 5)
                        offset_y = random.randint(-5, 5)
                        try:
                            # сначала переместимся к центру, потом сделаем микросдвиг
                            body = driver.find_element(By.TAG_NAME, "body")

                            ActionChains(driver) \
 \
                                .move_to_element_with_offset(body, vw // 2, vh // 2) \
 \
                                .move_by_offset(offset_x, offset_y) \
 \
                                .pause(random.uniform(0.01, 0.03)) \
 \
                                .perform()
                        except MoveTargetOutOfBoundsException:

                            pass  # если вдруг за границы — просто пропускаем

                # пауза так, чтобы суммарно уложиться в log_dt (ускорено через SPEED)

                base_interval = (log_dt / parts) / SPEED
                interval = random.uniform(base_interval * 0.8, base_interval * 1.2)
                time.sleep(max(interval, 0.02))
                # докручиваем остаток
                remaining = total - moved
                if abs(remaining) > 1:
                    driver.execute_script("window.scrollBy(0, arguments[0])", remaining)
                    time.sleep(random.uniform(0.05, 0.15))
                # финишная пауза
                time.sleep(random.uniform(0.02, 0.05))

            elif typ == "mouse_move":
                pts = data.get("positions", [])
                if pts:
                    perform_drag(driver, pts, data.get("pointerType", "mouse"))
                time.sleep(random.uniform(0.05, 0.15))

            elif typ in {"hover", "hover_generic"}:
                el = resolve_element(driver, data, timeout=0.5)
                # if not safe_hover(driver, el, data):
                #     log("hover skipped")
                if el:
                    synthetic_hover(driver, el, data)
                else:
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

    # --- здесь добавляем форвардер, если указан upstream-прокси ---
    effective_proxy = None
    if args.proxy:
        upstream = args.proxy
        port = _find_free_port()
        forward = subprocess.Popen([
            "proxy",  # proxy.py в PATH
            "--plugins", "proxy.plugin.proxy_pool.ProxyPoolPlugin",
            "--hostname", "127.0.0.1",
            "--port", str(port),
            "--proxy-pool", upstream,
            "--threaded",
        ])
        # гарантируем, что форвардер убьётся при закрытии скрипта
        atexit.register(lambda: (forward.terminate(), forward.wait()))
        time.sleep(0.5)
        effective_proxy = f"127.0.0.1:{port}"

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
        proxy=effective_proxy,
    )
