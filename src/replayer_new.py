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

import undetected_chromedriver as uc
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


@dataclass
class TabState:
    pending_url: str | None = None      # ждём first completed
    last_nav_ts: float = 0.0            # время последнего принятого completed_navigation
    last_user_ts: float = 0.0           # последний интерактивный эвент
    last_url: str = "about:blank"


step_counter = 0
FAIL_DIR = "replay_fails"
os.makedirs(FAIL_DIR, exist_ok=True)
tabs: dict[int, TabState] = defaultdict(TabState)


def log(msg: str):
    global step_counter
    stamp = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{stamp}][{step_counter:04d}] {msg}")
    sys.stdout.flush()


# ---------- helpers --------------------------------------------------------

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
        drv.execute_script("""
            [...document.querySelectorAll('[role="button"],button,div')]
              .flatMap(el => [...el.querySelectorAll('*'), el])
              .filter(el => /allow all|accept|принять|разрешить|согласен/i.test(el.textContent))
              .forEach(el => el.click());
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
    last_kill = time.time()
    skip_substrings = skip_substrings or set()

    events.sort(key=lambda e: e.get("timestamp", 0)) #  сортируем JSON инструкцию по timestamp чтобы реплей работал более стабильно и последовательно

    first_url: Dict[int, str] = {}
    for ev in events:
        raw = ev.get("data")
        if isinstance(raw, dict):
            u = raw.get("url") or raw.get("href")
            if u and not u.startswith(("chrome:", "about:")):
                first_url.setdefault(ev.get("tabId"), u)

    opts = uc.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")

    need_random = proxy is not None  # рандомим только если выходим через прокси

    if not user_agent:
        if need_random:
            try:
                ua = UserAgent()
                user_agent = ua.random
                log(f"[INFO] Randomly selected User-Agent: {user_agent}")
            except Exception as e:
                log(f"[Log ERROR] {e}")  # На всякий случай подставляем дефолтный user-agent, чтобы не упасть
                # user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                #               "Chrome/115.0.0.0 Safari/537.36")
                user_agent = settings.DEFAULT_UA
                log(f"[INFO] Default User-Agent from .env selected: {user_agent}")
        else:
            user_agent = user_agent = settings.DEFAULT_UA
            log(f"[INFO] Default User-Agent from .env selected: {user_agent}")
    if proxy:
        opts.add_argument(f"--proxy-server={proxy}")
    opts.add_argument(f"--user-agent={user_agent}")
    driver = uc.Chrome(options=opts)

    if cookies:
        domain = cookies[0].get("domain")
        target = next(iter(first_url.values()), f"https://{domain}") if domain else None
        if target:
            driver.get(target)
            for ck in cookies:
                try:
                    driver.add_cookie(ck)
                except Exception:
                    pass

    handles, prev_input = {}, None

    for ev in events:
        if time.time() - last_kill >= 5:
            cookie_killer(driver)
            last_kill = time.time()

        step_counter += 1
        typ = ev.get("type", "").lower()
        if any(sub in typ for sub in skip_substrings):
            log(f"{typ:>12s} (skipped)")
            continue

        log(f"{typ:>12s} Δ={ev.get('delta', 0):>4} ms")
        time.sleep(min(ev.get("delta", 150) / 1000, 0.5))

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
            if typ == "navigate_intent":
                href = data.get("href")
                if not href:
                    continue

                if data.get("tag") == "form" or data.get("role") == "search":
                    continue
                # отсекаем нефизические навигации
                if not data.get("was_recent_click"):
                    continue

                st = tabs[tab]

                # нормализуем полный URL
                href_full = normalize_href(href, st.last_url)
                # если дублирующий переход — пропускаем
                if st.pending_url == href_full:
                    log(f"    >>> duplicate NAV intent to '{href_full}', skipped")
                    continue
                st.pending_url = href_full

                method = "DIRECT"
                raw_el = resolve_element(driver, data, timeout=0.6)
                el = raw_el
                if el and el.tag_name.lower() != "a":
                    try:
                        el = el.find_element(By.XPATH, "./ancestor::a[1]")
                    except Exception:
                        el = raw_el

                if el:
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
                        driver.execute_script(
                            "const r = arguments[0].getBoundingClientRect();"
                            "window.scrollBy(0, r.top - window.innerHeight/2);", el)
                        ActionChains(driver).move_to_element(el).click().perform()
                        method = "LINK"
                    except Exception as e:
                        log(f"[DEBUG] click via LINK failed: {e}")
                        el = None

                if not el and data.get("boundingRect"):
                    bbox = data["boundingRect"]
                    try:
                        perform_click(driver, bbox.get("x", 0) + 3, bbox.get("y", 0) + 3)
                        method = "COORD"
                    except Exception:
                        pass

                if method == "DIRECT":
                    driver.get(href_full)
                wait_for_dom_ready(driver)
                st.last_url = driver.current_url
                log(f"    >>> NAV via {method}")
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

    final_cookies = driver.get_cookies()
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
