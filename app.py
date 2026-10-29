import os
import json
import re
import sqlite3
import asyncio
import traceback
from datetime import datetime, timedelta
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from flask import Flask, render_template, Response, request, stream_with_context, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "moodle.db")
USER_DATA_DIR = os.path.join(SCRIPT_DIR, "playwright_google_profile")

MOODLE_URL = "https://learn.vcs.net"
IGNORED_CATEGORIES = {"VCHS Home", "Staff Development"}

discovered_courses = []
scraped_cache = []

app = Flask(__name__)
app.secret_key = b"_5#y2L\"F4Q8z\n\xec]/"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)


# --- Helpers & Cleaners ---

def clean_course_title(title):
    if not title:
        return ""
    cleaned = re.sub(r'^course\s*name\s*', '', title, flags=re.IGNORECASE).strip()

    mid = len(cleaned) // 2
    if len(cleaned) > 0:
        first_half = cleaned[:mid].strip()
        second_half = cleaned[mid:].strip()
        if first_half.lower() == second_half.lower():
            cleaned = first_half

    parts = [p.strip() for p in re.split(r'\s+[-–]\s+', cleaned) if p.strip()]
    unique_parts = []
    for p in parts:
        if not any(p.lower() == existing.lower() for existing in unique_parts):
            unique_parts.append(p)
    return " - ".join(unique_parts)


# --- Database Helpers ---

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                app_password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS configurations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                url TEXT NOT NULL,
                schedule_day TEXT NOT NULL DEFAULT 'A',
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );
        """)
        conn.commit()


init_db()


# --- Authentication & Config Routes ---

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()
    raw_app_pass = data.get("app_password", "").strip()

    if not email or not password or not raw_app_pass:
        return jsonify({"error": "Email, account password, and Google App Password are required."}), 400

    if len(password) < 6:
        return jsonify({"error": "Account password must be at least 6 characters long."}), 400

    cleaned_app_pass = raw_app_pass.replace(" ", "")
    if len(cleaned_app_pass) != 16:
        return jsonify({
            "error": "Invalid App Password format. A Google App Password must be exactly 16 characters long."
        }), 400

    password_hash = generate_password_hash(password)

    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, app_password) VALUES (?, ?, ?)",
                (email, password_hash, cleaned_app_pass)
            )
            conn.commit()
            user_id = cursor.lastrowid

        session.permanent = True
        session["user_id"] = user_id
        session["email"] = email
        return jsonify({"success": True, "email": email})
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with this email address already exists."}), 409


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()

    if not email or not password:
        return jsonify({"error": "Email and account password are required."}), 400

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if user and check_password_hash(user["password_hash"], password):
        session.permanent = True
        session["user_id"] = user["id"]
        session["email"] = user["email"]
        return jsonify({"success": True, "email": user["email"]})

    return jsonify({"error": "Invalid email or account password."}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/delete-account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    session.clear()
    return jsonify({"success": True})


@app.route("/api/me", methods=["GET"])
def me():
    if "user_id" in session:
        return jsonify({"authenticated": True, "email": session.get("email")})
    return jsonify({"authenticated": False})


@app.route("/api/config", methods=["GET", "POST", "DELETE"])
def user_config():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]

    if request.method == "GET":
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM configurations WHERE user_id = ?", (user_id,)).fetchall()
        if not rows:
            return jsonify({"has_config": False, "courses": []})
        courses = [dict(row) for row in rows]
        return jsonify({"has_config": True, "courses": courses})

    elif request.method == "POST":
        data = request.get_json() or {}
        courses = data.get("courses", [])

        if not courses:
            return jsonify({"error": "No course configuration provided."}), 400

        with get_db() as conn:
            conn.execute("DELETE FROM configurations WHERE user_id = ?", (user_id,))
            for c in courses:
                conn.execute(
                    "INSERT INTO configurations (user_id, title, category, url, schedule_day) VALUES (?, ?, ?, ?, ?)",
                    (user_id, c["title"], c["category"], c["url"], c.get("schedule_day", "A"))
                )
            conn.commit()

        return jsonify({"success": True})

    elif request.method == "DELETE":
        with get_db() as conn:
            conn.execute("DELETE FROM configurations WHERE user_id = ?", (user_id,))
            conn.commit()
        return jsonify({"success": True})


# --- Scraping Logic ---

def parse_date_by_schedule(text, is_b_day=False):
    pattern = r'([A-Za-z]+)\s+(\d{1,2})(?:/(\d{1,2}))?'
    match = re.search(pattern, text)
    if not match:
        return None

    groups = match.groups()
    month_str = groups[0]
    first_day = groups[1]
    second_day = groups[2]

    target_day = second_day if (is_b_day and second_day) else first_day
    current_year = datetime.now().year

    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(f"{month_str} {target_day} {current_year}", fmt)
        except ValueError:
            continue
    return None


async def select_best_toc_item(page, is_b_day=False):
    toc_container = page.locator("div.book_toc.book_toc_indented.clearfix > ul")
    if await toc_container.count() == 0:
        return None

    li_elements = toc_container.locator("> li")
    count = await li_elements.count()
    if count == 0:
        return None

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = []

    for i in range(count):
        li = li_elements.nth(i)
        strong_elem = li.locator("strong")
        a_elem = li.locator("a")

        text = ""
        elem_type = "strong"
        href = None

        if await strong_elem.count() > 0:
            text = (await strong_elem.first.inner_text()).strip()
            elem_type = "strong"
        elif await a_elem.count() > 0:
            text = (await a_elem.first.inner_text()).strip()
            href = await a_elem.first.get_attribute("href")
            elem_type = "a"

        if text:
            parsed_date = parse_date_by_schedule(text, is_b_day=is_b_day)
            if parsed_date:
                candidates.append({
                    "index": i,
                    "date": parsed_date,
                    "type": elem_type,
                    "href": href,
                    "text": text
                })

    valid_candidates = [c for c in candidates if c["date"] >= today]

    if not valid_candidates:
        return None

    valid_candidates.sort(key=lambda x: x["date"])
    current_candidate = valid_candidates[0]

    current_idx = current_candidate["index"]
    previous_candidate = None
    for c in candidates:
        if c["index"] == current_idx - 1:
            previous_candidate = c
            break

    return {
        "current": current_candidate,
        "previous": previous_candidate
    }


def clean_truncated_text(txt):
    if not txt:
        return "", False

    should_stop = False
    if "◀︎" in txt:
        txt = txt.split("◀︎")[0]
        should_stop = True

    txt = re.sub(r'[\s\[\(\]\)]+$', '', txt).strip()
    return txt, should_stop


def format_node_text(element, base_url):
    elem_copy = BeautifulSoup(str(element), "html.parser")
    for a_tag in elem_copy.find_all("a", href=True):
        href = a_tag["href"]
        if not href.startswith("http"):
            href = urljoin(base_url, href)
        link_text = a_tag.get_text(strip=True)
        if link_text:
            a_tag.replace_with(f" [{link_text}]({href}) ")

    raw_text = elem_copy.get_text()
    cleaned_text = re.sub(r'[ \t]+', ' ', raw_text)
    return cleaned_text.strip()


def format_list_element(element, base_url, indent_level=0):
    indent = "  " * indent_level
    lines = []

    for child in element.children:
        if not getattr(child, "name", None):
            continue

        if child.name == "li":
            li_copy = BeautifulSoup(str(child), "html.parser")
            for sub in li_copy.find_all(["ul", "ol"]):
                sub.decompose()

            direct_text = format_node_text(li_copy, base_url)
            direct_text, _ = clean_truncated_text(direct_text)

            if direct_text:
                prefix = "*" if element.name == "ul" else "1."
                lines.append(f"{indent}{prefix} {direct_text}")

            for sub_list in child.find_all(["ul", "ol"], recursive=False):
                sub_lines = format_list_element(sub_list, base_url, indent_level + 1)
                if sub_lines:
                    lines.append(sub_lines)

    return "\n".join(lines)


async def extract_headers_direct(page):
    selectors = [
        "section#region-main.region-main-content[aria-label='Content']",
        "section#region-main",
        "div#region-main",
        "div[role='main']",
        "div#page-content"
    ]

    target_elem = None
    for selector in selectors:
        elem = page.locator(selector)
        if await elem.count() > 0 and await elem.first.is_visible():
            target_elem = elem.first
            break

    if not target_elem:
        return {"objectives": "N/A", "classroom_activities": "N/A", "homework": "N/A"}

    raw_html = await target_elem.inner_html()
    soup = BeautifulSoup(raw_html, "html.parser")

    results = {
        "objectives": "N/A",
        "classroom_activities": "N/A",
        "homework": "N/A"
    }

    def identify_header(text):
        if not text:
            return None
        trimmed = text.strip().lower()
        if re.search(r"^objectives?:?", trimmed) or trimmed == "objectives":
            return "objectives"
        if re.search(r"^classroom activities?:?", trimmed) or trimmed == "classroom activities" or re.search(r"^activities?:?", trimmed):
            return "classroom_activities"
        if re.search(r"^homework:?", trimmed) or trimmed == "homework" or re.search(r"^assignments?:?", trimmed):
            return "homework"
        return None

    header_tags = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "strong", "div", "b"])
    found_headers = []

    for tag in header_tags:
        text = tag.get_text(strip=True)
        if 0 < len(text) < 60:
            key = identify_header(text)
            if key:
                if not any(tag in existing["tag"].parents for existing in found_headers):
                    found_headers.append({"tag": tag, "key": key})

    base_page_url = page.url

    for idx, item in enumerate(found_headers):
        key = item["key"]
        header_tag = item["tag"]
        next_header_tag = found_headers[idx + 1]["tag"] if idx + 1 < len(found_headers) else None

        collected_lines = []
        stop_extraction = False

        for element in header_tag.find_all_next():
            if stop_extraction:
                break

            if not getattr(element, "name", None):
                continue

            if next_header_tag and (element == next_header_tag or next_header_tag in element.parents):
                break

            if element.name in ["ul", "ol"] and element.find_parent(["ul", "ol"]):
                continue

            if element.name in ["ul", "ol"]:
                formatted_list = format_list_element(element, base_page_url, indent_level=0)
                if formatted_list and formatted_list not in collected_lines:
                    collected_lines.append(formatted_list)

            elif element.name in ["p", "div", "span"] and not element.find_parent(["ul", "ol"]):
                if not element.find(["p", "ul", "ol", "div"]):
                    txt = format_node_text(element, base_page_url)
                    txt, should_stop = clean_truncated_text(txt)

                    if txt and txt not in collected_lines and identify_header(txt) is None:
                        collected_lines.append(txt)

                    if should_stop:
                        stop_extraction = True
                        break

        if collected_lines:
            results[key] = "\n".join(collected_lines)

    return results


async def find_quarter_2_url(page):
    links = page.locator("a.aalink.stretched-link:has(span.instancename)")
    year_patterns = [
        "Quarter 2 (2026-27)",
        "Quarter 2 2026-27",
        "Quarter 2 (2026-2027)",
        "Quarter 2 2026-2027"
    ]
    count = await links.count()
    for i in range(count):
        link_elem = links.nth(i)
        span_elem = link_elem.locator("span.instancename")
        if await span_elem.count() > 0:
            span_text = (await span_elem.first.inner_text()).strip()
            if any(pattern in span_text for pattern in year_patterns):
                return await link_elem.get_attribute("href")
    return None


async def scrape_single_course(context, item, is_b_day, semaphore, queue):
    """Worker task that scrapes a single course and pushes progress events to the queue."""
    async with semaphore:
        page = await context.new_page()
        title = item["title"]
        category = item["category"]
        url = item["url"]

        res_data = None
        try:
            await page.goto(url)
            await page.wait_for_load_state("domcontentloaded")

            target_url = None
            lesson_tab = page.locator("a.nav-link:has-text('Lesson Plan')")
            if await lesson_tab.count() > 0 and await lesson_tab.first.is_visible():
                await lesson_tab.first.click()
                await page.wait_for_load_state("domcontentloaded")
                target_url = await find_quarter_2_url(page)

            if not target_url:
                target_url = await find_quarter_2_url(page)

            if not target_url:
                await page.close()
                res_data = {
                    "id": item["id"],
                    "title": title,
                    "category": category,
                    "status": "Poor Maintenance",
                    "extracted": None
                }
            else:
                await page.goto(target_url)
                await page.wait_for_load_state("domcontentloaded")

                best_items = await select_best_toc_item(page, is_b_day=is_b_day)

                if not best_items or not best_items.get("current"):
                    await page.close()
                    res_data = {
                        "id": item["id"],
                        "title": title,
                        "category": category,
                        "status": "Poor Maintenance",
                        "extracted": None
                    }
                else:
                    current_candidate = best_items["current"]
                    previous_candidate = best_items.get("previous")

                    if current_candidate["type"] == "a" and current_candidate.get("href"):
                        full_href = urljoin(page.url, current_candidate["href"])
                        await page.goto(full_href)
                        await page.wait_for_load_state("domcontentloaded")

                    current_data = await extract_headers_direct(page)
                    homework_assigned = current_data.get("homework", "N/A")

                    homework_due = "N/A"
                    if previous_candidate and previous_candidate.get("href"):
                        full_href_prev = urljoin(page.url, previous_candidate["href"])
                        await page.goto(full_href_prev)
                        await page.wait_for_load_state("domcontentloaded")
                        prev_data = await extract_headers_direct(page)
                        homework_due = prev_data.get("homework", "N/A")

                    await page.close()
                    res_data = {
                        "id": item["id"],
                        "title": title,
                        "category": category,
                        "status": "Success",
                        "extracted": {
                            "objectives": current_data.get("objectives", "N/A"),
                            "classroom_activities": current_data.get("classroom_activities", "N/A"),
                            "homework_due": homework_due,
                            "homework_assigned": homework_assigned
                        }
                    }
        except Exception as e:
            print(f"[ERROR] Concurrent exception for '{title}': {e}")
            traceback.print_exc()
            try:
                await page.close()
            except Exception:
                pass
            res_data = {
                "id": item["id"],
                "title": title,
                "category": category,
                "status": "Poor Maintenance",
                "extracted": None
            }

        await queue.put(res_data)
        return res_data


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stream-discover")
def stream_discover():
    def send_event(event_type, payload):
        return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"

    if "user_id" not in session:
        def unauth():
            yield send_event("status", "Error: Unauthenticated. Please log in first.")
        return Response(stream_with_context(unauth()), mimetype="text/event-stream")

    user_id = session["user_id"]
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    if not user:
        def no_user():
            yield send_event("status", "Error: Account not found in database.")
        return Response(stream_with_context(no_user()), mimetype="text/event-stream")

    user_email = user["email"]
    user_app_password = user["app_password"]

    async def discover_async():
        global discovered_courses
        discovered_courses = []

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=True,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(MOODLE_URL)

            user_menu = page.locator("#action-menu-toggle-0")
            if not await user_menu.is_visible():
                await page.goto(f"{MOODLE_URL}/login/index.php")
                google_btn = page.locator("a:has-text('Student Login'), button:has-text('Student Login')")
                if await google_btn.is_visible():
                    await google_btn.click()

                await page.wait_for_timeout(3000)
                if "accounts.google.com" in page.url:
                    email_input = page.locator("input[type='email']")
                    if await email_input.is_visible():
                        await email_input.fill(user_email)
                        await page.locator("#identifierNext").click()
                        await page.wait_for_timeout(2500)

                    password_input = page.locator("input[type='password']")
                    if await password_input.is_visible():
                        await password_input.fill(user_app_password)
                        await page.locator("#passwordNext").click()

                await user_menu.wait_for(state="visible", timeout=120000)

            side_panel_btn = page.locator("#side-panel-button")
            await side_panel_btn.wait_for(state="visible", timeout=10000)
            await side_panel_btn.click()
            await page.wait_for_timeout(1000)

            courses_link = page.locator("a[href*='courses.php']").first
            if await courses_link.is_visible():
                await courses_link.click()
            else:
                await page.goto("https://learn.vcs.net/my/courses.php")

            await page.wait_for_load_state("domcontentloaded")

            containers = page.locator("div.col.d-flex.px-0.mb-2")
            try:
                await containers.first.wait_for(state="visible", timeout=10000)
            except Exception:
                await context.close()
                return []

            count = await containers.count()
            for i in range(count):
                container = containers.nth(i)
                category_span = container.locator("span.categoryname.text-truncate")
                if await category_span.count() > 0:
                    category_text = (await category_span.first.inner_text()).strip()
                    if category_text in IGNORED_CATEGORIES:
                        continue
                    course_anchor = container.locator("a.aalink.coursename.me-2.mb-1")
                    if await course_anchor.count() > 0:
                        href = await course_anchor.first.get_attribute("href")
                        title = (await course_anchor.first.inner_text()).strip()
                        cleaned_title = clean_course_title(title)

                        if href:
                            discovered_courses.append({
                                "id": len(discovered_courses),
                                "title": cleaned_title,
                                "category": category_text,
                                "url": href
                            })

            await context.close()
            return discovered_courses

    def generate_events():
        yield send_event("status", f"Launching background browser for {user_email}...")
        results = asyncio.run(discover_async())
        yield send_event("status", f"Found {len(results)} course(s). Ready for schedule selection.")
        yield send_event("discovery_complete", results)

    return Response(stream_with_context(generate_events()), mimetype="text/event-stream")


@app.route("/stream-scrape")
def stream_scrape():
    def send_event(event_type, payload):
        return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"

    if "user_id" not in session:
        def unauth():
            yield send_event("status", "Error: Unauthenticated. Please log in first.")
        return Response(stream_with_context(unauth()), mimetype="text/event-stream")

    user_id = session["user_id"]
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        saved_configs = conn.execute("SELECT * FROM configurations WHERE user_id = ?", (user_id,)).fetchall()

    if not user:
        def no_user():
            yield send_event("status", "Error: Account not found in database.")
        return Response(stream_with_context(no_user()), mimetype="text/event-stream")

    user_email = user["email"]
    user_app_password = user["app_password"]

    schedules_json = request.args.get("schedules", "{}")
    try:
        schedules = json.loads(schedules_json)
    except Exception:
        schedules = {}

    target_courses = []
    if saved_configs:
        for idx, row in enumerate(saved_configs):
            target_courses.append({
                "id": idx,
                "title": row["title"],
                "category": row["category"],
                "url": row["url"],
                "schedule_day": row["schedule_day"]
            })
    else:
        target_courses = discovered_courses

    total_count = len(target_courses)

    async def run_concurrent_streaming(out_queue):
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=True,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(MOODLE_URL)

            user_menu = page.locator("#action-menu-toggle-0")
            if not await user_menu.is_visible():
                await page.goto(f"{MOODLE_URL}/login/index.php")
                google_btn = page.locator("a:has-text('Student Login'), button:has-text('Student Login')")
                if await google_btn.is_visible():
                    await google_btn.click()

                await page.wait_for_timeout(3000)
                if "accounts.google.com" in page.url:
                    email_input = page.locator("input[type='email']")
                    if await email_input.is_visible():
                        await email_input.fill(user_email)
                        await page.locator("#identifierNext").click()
                        await page.wait_for_timeout(2500)

                    password_input = page.locator("input[type='password']")
                    if await password_input.is_visible():
                        await password_input.fill(user_app_password)
                        await page.locator("#passwordNext").click()

                await user_menu.wait_for(state="visible", timeout=120000)

            await page.close()

            semaphore = asyncio.Semaphore(4)
            progress_queue = asyncio.Queue()
            worker_tasks = []

            for item in target_courses:
                idx = item["id"]
                if saved_configs:
                    is_b_day = (item.get("schedule_day") == "B")
                else:
                    is_b_day = (schedules.get(str(idx)) == "B" or schedules.get(idx) == "B")

                worker_tasks.append(
                    asyncio.create_task(
                        scrape_single_course(context, item, is_b_day, semaphore, progress_queue)
                    )
                )

            collected_results = []
            for _ in range(total_count):
                res = await progress_queue.get()
                collected_results.append(res)
                out_queue.put_nowait({"type": "course_complete", "data": res})

            await asyncio.gather(*worker_tasks)
            await context.close()

            collected_results.sort(key=lambda x: x["id"])
            out_queue.put_nowait({"type": "scrape_complete", "data": collected_results})

    def generate_events():
        import queue
        out_queue = queue.Queue()

        yield send_event("status", {"total": total_count})

        def thread_target():
            asyncio.run(run_concurrent_streaming(out_queue))

        import threading
        thread = threading.Thread(target=thread_target)
        thread.start()

        while True:
            try:
                msg = out_queue.get(timeout=0.5)
                msg_type = msg["type"]
                payload = msg["data"]

                if msg_type == "course_complete":
                    yield send_event("course_complete", payload)
                elif msg_type == "scrape_complete":
                    yield send_event("status", "Concurrent extraction complete.")
                    yield send_event("scrape_complete", payload)
                    break
            except queue.Empty:
                continue

        thread.join()

    return Response(stream_with_context(generate_events()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=5000)