#!/usr/bin/env python3
# replayer.py — Selenium-based re-player with fast mode, UA, cookies & proxy support

import sys
import os
import json
import time
import datetime
import random
from html import unescape
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Set, Tuple
from fake_useragent import UserAgent

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)

# ─────────────── Globals ───────────────
step_counter = 0
FAIL_DIR = "replay_fails"
os.makedirs(FAIL_DIR, exist_ok=True)


# ─────────────────── Logging ───────────────────
def log(msg: str):
    global step_counter
    stamp = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{stamp}][{step_counter:04d}] {msg}")
    sys.stdout.flush()


# ─────────────────── Helpers ───────────────────
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
        try:
            ctx = host.shadow_root
        except AttributeError:
            ctx = host.parent.execute_script("return arguments[0].shadowRoot", host)
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


def resolve_element(driver, data: Dict[str, Any], timeout: float = 4):
    chain = data.get("frameChain", [])
    shadow = data.get("shadowPath", [])

    def _find(_):
        try:
            switch_to_frame_chain(driver, chain)
            ctx = driver
            if shadow:
                ctx = enter_shadow_path(ctx, shadow)
            return find_in_context(ctx, data)
        except Exception:
            return None

    try:
        return WebDriverWait(driver, timeout).until(_find)
    except TimeoutException:
        return None


def perform_click(driver, x: int, y: int):
    ActionChains(driver).move_by_offset(0, 0).move_by_offset(x, y).click().perform()


def perform_drag(driver, seq: List[Dict[str, int]], pointer_type: str = "mouse"):
    if not seq:
        return
    actions = ActionBuilder(driver)
    mouse = PointerInput(
        PointerInput.MOUSE if pointer_type == "mouse" else PointerInput.PEN,
        "drag"
    )
    actions.add_action(mouse)
    start = seq[0]
    mouse.move_to_location(start["x"], start["y"])
    mouse.pointer_down()
    for pt in seq[1:]:
        mouse.move_to_location(pt["x"], pt["y"], origin="pointer")
    mouse.pointer_up()
    actions.perform()


def safe_hover(driver, el, data) -> bool:
    if not el:
        return False
    try:
        if el.is_displayed() and el.size["width"] and el.size["height"]:
            ActionChains(driver).move_to_element(el).perform()
            return True
    except WebDriverException:
        pass
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
                      .flatMap(el => [...el.querySelectorAll('*'), el])       // сам элемент + все его потомки
                      .filter(el => /allow all|accept|принять|разрешить|согласен/i.test(el.textContent))
                      .forEach(el => el.click());
                """)
    except Exception:
        pass


# ─────────────────── Core replay ───────────────────

def replay_events(
        events: List[Dict[str, Any]],
        skip_substrings: Optional[Set[str]] = None,
        user_agent: Optional[str] = None,
        cookies: Optional[List[Dict[str, Any]]] = None,
        proxy: Optional[str] = None
) -> Tuple[list[Dict[str, Any]], str]:
    global step_counter, last_kill
    last_kill = time.time()

    events.sort(key=lambda e: e.get("timestamp", 0)) #  сортируем JSON инструкцию по timestamp чтобы реплей работал более стабильно и последовательно

    if skip_substrings is None:
        skip_substrings = set()

    # collect first URLs per tab
    first_url: Dict[int, str] = {}
    for ev in events:
        raw = ev.get("data")
        if not isinstance(raw, dict):
            continue
        u = raw.get("url") or raw.get("href")
        if u and not u.startswith(("chrome:", "about:")):
            first_url.setdefault(ev.get("tabId"), u)

    # prepare Chrome options
    opts = uc.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")

    if not user_agent:
        try:
            ua = UserAgent()
            user_agent = ua.random
            log(f"[INFO] Randomly selected User-Agent: {user_agent}")
        except Exception as e:
            log(f"[Log ERROR] {e}")  # На всякий случай подставляем дефолтный user-agent, чтобы не упасть
            user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/115.0.0.0 Safari/537.36")
    else:
        log(f"[INFO] User-Agent from args: {user_agent}")

    # if user_agent:
    #     opts.add_argument(f"--user-agent={user_agent}")
    if proxy:
        opts.add_argument(f"--proxy-server={proxy}")
    opts.add_argument(f"--user-agent={user_agent}")
    driver = uc.Chrome(options=opts)

    # set cookies if provided
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

    handles: Dict[int, str] = {}
    last_url: Dict[int, str] = {}
    prev_input: Optional[str] = None

    for ev in events:
        if time.time() - last_kill >= 5:
            cookie_killer(driver)
            last_kill = time.time()

        step_counter += 1
        typ = ev.get("type", "")
        low = typ.lower()
        if any(sub in low for sub in skip_substrings):
            log(f"{typ:>10s} (skipped)")
            continue

        log(f"{typ:>10s} Δ={ev.get('delta', 0):>4} ms")
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
                last_url[tab] = url0

        driver.switch_to.window(handles[tab])
        data = ev.get("data", {}) or {}

        try:
            # NAVIGATION
            if typ == "navigate_intent":
                href = data.get("href")
                if data.get("was_recent_click") and href:
                    driver.get(normalize_href(href, last_url.get(tab, href)))
                    wait_for_dom_ready(driver)
                    last_url[tab] = driver.current_url
                continue

            if typ == "completed_navigation":
                last_url[tab] = data.get("url", driver.current_url)
                continue

            # ACTIONS
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
                key = data.get("key", "")
                mods = []
                if data.get("ctrlKey"): mods.append(Keys.CONTROL)
                if data.get("shiftKey"): mods.append(Keys.SHIFT)
                if data.get("altKey"): mods.append(Keys.ALT)
                if data.get("metaKey"): mods.append(Keys.COMMAND)
                act.send_keys(*mods, key)

            elif typ == "input":
                el = resolve_element(driver, data)
                if not el:
                    continue
                sel = data.get("selector") or build_combined_selector(data) or ""
                val = data.get("value", "")
                cur = el.get_attribute("value") or ""
                if prev_input == sel and val.startswith(cur):
                    el.send_keys(val[len(cur):])
                else:
                    el.clear()
                    el.send_keys(val)
                prev_input = sel

            elif typ == "scroll":
                driver.execute_script(
                    "window.scrollTo(arguments[0], arguments[1]);",
                    data.get("x", 0),
                    data.get("y", 0),
                )

            elif typ == "wheel":
                dy = data.get("deltaY", data.get("y", 0))
                driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
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
    time.sleep(1)
    driver.quit()
    return final_cookies, final_user_agent


# ───────────────────── CLI Entrypoint ─────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Replay browser session (fast mode)")
    parser.add_argument("--log", default="sorted_log.json", help="Path to events JSON")
    parser.add_argument("--skip", default="", help="Comma-separated substrings to skip")
    parser.add_argument("--user-agent", dest="ua", help="User-Agent string to set on the browser")
    parser.add_argument("--cookies", help="Path to JSON file with cookies")
    parser.add_argument("--proxy", help="Proxy URL, e.g. http://user:pass@host:port")
    args = parser.parse_args()

    skip_set: Set[str] = {s.strip().lower() for s in args.skip.split(",") if s.strip()}

    cookies_list: List[Dict[str, Any]] = []
    if args.cookies:
        try:
            with open(args.cookies, "r", encoding="utf-8") as f:
                cookies_list = json.load(f)
        except Exception as e:
            log(f"[Cookie ERROR] {e}")
            sys.exit(1)

    try:
        with open(args.log, "r", encoding="utf-8") as f:
            evs = json.load(f)
    except Exception as e:
        log(f"[Log ERROR] {e}")
        sys.exit(1)

    user_agent = args.ua
    if not user_agent:
        try:
            ua = UserAgent()
            user_agent = ua.random
            log(f"[INFO] Randomly selected User-Agent: {user_agent}")
        except Exception as e:
            log(f"[Log ERROR] {e}")  # На всякий случай подставляем дефолтный user-agent, чтобы не упасть
            user_agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/115.0.0.0 Safari/537.36")
    else:
        log(f"[INFO] User-Agent from args: {user_agent}")

    evs.sort(key=lambda e: e.get("timestamp", 0))

    replay_events(
        events=evs,
        skip_substrings=skip_set,
        user_agent=user_agent,
        cookies=cookies_list,
        proxy=args.proxy
    )
