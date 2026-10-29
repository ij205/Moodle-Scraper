import os
import time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env.local")

load_dotenv(dotenv_path=ENV_PATH)

EMAIL = os.getenv("GOOGLE_EMAIL")
APP_PASSWORD = os.getenv("GOOGLE_PASSWORD")
MOODLE_URL = "https://learn.vcs.net"

USER_DATA_DIR = os.path.join(SCRIPT_DIR, "playwright_google_profile")

# List of category names to ignore
IGNORED_CATEGORIES = {"VCHS Home", "Staff Development"}


def find_quarter_2_url(page):
    """Searches the current page for a link with a span matching the Quarter 2 criteria."""
    links = page.locator("a.aalink.stretched-link:has(span.instancename)")
    
    # List of year format variations to match
    year_patterns = [
        "Quarter 2 (2026-27)",
        "Quarter 2 2026-27",
        "Quarter 2 (2026-2027)",
        "Quarter 2 2026-2027"
    ]
    
    for i in range(links.count()):
        link_elem = links.nth(i)
        span_elem = link_elem.locator("span.instancename")
        if span_elem.count() > 0:
            span_text = span_elem.first.inner_text().strip()
            if any(pattern in span_text for pattern in year_patterns):
                return link_elem.get_attribute("href")
    return None


def process_course_page(page, course_url, category):
    """Navigates to a course, checks Lesson Plans tab or current page for Q2, and prints nested section content."""
    page.goto(course_url)
    page.wait_for_load_state("domcontentloaded")

    # Get course title
    title_heading = page.locator("h1.h2.mb-0")
    try:
        title_heading.wait_for(state="visible", timeout=5000)
        course_title = title_heading.inner_text().strip()
    except Exception:
        course_title = "Unknown Course Title"

    target_url = None

    # Check for any nav-link containing "Lesson Plan"
    lesson_tab = page.locator("a.nav-link:has-text('Lesson Plan')")
    
    if lesson_tab.count() > 0 and lesson_tab.first.is_visible():
        lesson_tab.first.click()
        page.wait_for_load_state("domcontentloaded")
        target_url = find_quarter_2_url(page)

    # Fallback: Check for Quarter 2 link directly on the current course page
    if not target_url:
        target_url = find_quarter_2_url(page)

    # If Quarter 2 link is still not found anywhere
    if not target_url:
        print(f"Course: {course_title} -> Poor Maintenance")
        return

    # Navigate to the Quarter 2 page
    page.goto(target_url)
    page.wait_for_load_state("domcontentloaded")

    # Extract text from <section> strictly nested inside #page-content -> #region-main-box.region-main
    section_selector = (
        "div#page-content "
        "div#region-main-box.region-main "
        "section#region-main.region-main-content[aria-label='Content']"
    )
    section_elem = page.locator(section_selector)

    try:
        section_elem.wait_for(state="visible", timeout=5000)
        section_text = section_elem.inner_text().strip()
        print(f"Course: {course_title} ->\n{section_text}\n" + "-" * 50)
    except Exception:
        print(f"Course: {course_title} -> Poor Maintenance (Nested section content not found on Q2 page)")


def navigate_to_courses(page):
    """Navigates to My Courses, filters categories, and processes valid courses."""
    print("Opening side panel...")
    side_panel_btn = page.locator("#side-panel-button")
    side_panel_btn.wait_for(state="visible", timeout=10000)
    side_panel_btn.click()

    page.wait_for_timeout(1000)

    print("Navigating to My Courses...")
    courses_link = page.locator("a[href*='courses.php']").first
    
    if courses_link.is_visible():
        courses_link.click()
    else:
        print("Link not interactable; navigating directly...")
        page.goto("https://learn.vcs.net/my/courses.php")

    page.wait_for_load_state("domcontentloaded")
    print(f"Successfully arrived at: {page.url}\n")

    containers = page.locator("div.col.d-flex.px-0.mb-2")
    
    try:
        containers.first.wait_for(state="visible", timeout=10000)
    except Exception:
        print("No course containers found on the page.")
        return

    count = containers.count()
    print(f"Found {count} course container(s). Extracting course links...\n")

    # Collect valid course URLs first
    course_urls = []
    for i in range(count):
        container = containers.nth(i)
        category_span = container.locator("span.categoryname.text-truncate")
        
        if category_span.count() > 0:
            category_text = category_span.first.inner_text().strip()
            
            if category_text in IGNORED_CATEGORIES:
                continue
            
            course_anchor = container.locator("a.aalink.coursename.me-2.mb-1")
            if course_anchor.count() > 0:
                href = course_anchor.first.get_attribute("href")
                if href:
                    course_urls.append((category_text, href))

    print(f"Processing {len(course_urls)} relevant course page(s):\n")

    # Process each course page
    for index, (category, url) in enumerate(course_urls, start=1):
        print(f"[{index}] Checking course in '{category}'...")
        process_course_page(page, url, category)


def login_and_access_moodle():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = context.pages[0] if context.pages else context.new_page()

        print(f"Navigating to {MOODLE_URL}...")
        page.goto(MOODLE_URL)

        user_menu = page.locator("#action-menu-toggle-0")

        if user_menu.is_visible():
            print("Already logged in via saved persistent session!")
            navigate_to_courses(page)
        else:
            print("Session not active. Navigating directly to login page...")
            page.goto(f"{MOODLE_URL}/login/index.php")

            google_btn = page.locator("a:has-text('Student Login'), button:has-text('Student Login')")
            if google_btn.is_visible():
                print("Clicking Google OAuth button...")
                google_btn.click()
            else:
                print("Google button not automatically detected. Please click it in the browser.")

            page.wait_for_timeout(3000)
            if "accounts.google.com" in page.url:
                print("Handling Google email step...")
                email_input = page.locator("input[type='email']")
                if email_input.is_visible():
                    email_input.fill(EMAIL)
                    page.locator("#identifierNext").click()
                    page.wait_for_timeout(2500)

                print("Handling Google password step...")
                password_input = page.locator("input[type='password']")
                if password_input.is_visible():
                    password_input.fill(APP_PASSWORD)
                    page.locator("#passwordNext").click()

            print("\n" + "=" * 60)
            print("ACTION REQUIRED: Complete any 2FA or CAPTCHA prompts directly in Chrome.")
            print("Waiting for login verification...")
            print("=" * 60 + "\n")

            try:
                user_menu.wait_for(state="visible", timeout=120000)
                print("Login verified successfully!")
                navigate_to_courses(page)
            except Exception:
                print("Timed out waiting for logged-in state. Current URL:", page.url)

        time.sleep(5)
        context.close()


if __name__ == "__main__":
    login_and_access_moodle()