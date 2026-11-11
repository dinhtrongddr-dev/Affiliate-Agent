import logging
import time

from playwright.sync_api import sync_playwright

from .settings import get_settings


def main() -> int:
    settings = get_settings()
    profile_dir = settings.project_root / "profiles" / "shopee"
    profile_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("Opening a dedicated Shopee Chrome profile.")
    print("Log in manually. Complete OTP/CAPTCHA yourself if Shopee requests it.")
    print("Do not share passwords or cookies in ChatGPT.")
    print("Close this Chrome window after Creator Center is visible to save the profile.")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            locale="vi-VN",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            "https://banhang.shopee.vn/creator-center/video-upload/upload",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        try:
            while context.pages:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            context.close()

    print(f"Shopee profile saved locally at: {profile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
