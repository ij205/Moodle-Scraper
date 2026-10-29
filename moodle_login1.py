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


def login_to_moodle():
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
            except Exception:
                print("Timed out waiting for logged-in state. Current URL:", page.url)

        time.sleep(5)
        context.close()


if __name__ == "__main__":
    login_to_moodle()