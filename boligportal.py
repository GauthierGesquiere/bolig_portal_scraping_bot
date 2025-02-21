from playwright.async_api import async_playwright
import requests
import asyncio
import re
import os
import requests
import time
import logging

from pathlib import Path
from dotenv import load_dotenv

load_dotenv("credentials.env")

# Configuration
BOLIGPORTAL_EMAIL = os.getenv("BOLIGPORTAL_EMAIL")
BOLIGPORTAL_PASSWORD = os.getenv("BOLIGPORTAL_PASSWORD")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CONTACTED_FILE = "visited_links.txt"
SEARCH_LOCATION = "Copenhagen, Denmark"  # Your preferred location
MAX_DISTANCE_METERS = 10000  # Maximum acceptable distance (in meters)
MAX_PRICE = 30000
MIN_ROOMS = 5  # Minimum number of rooms required
MESSAGE_TEMPLATE = "Hello, I'm interested in this listing. Is it still available?"
MAX_NR_APARTMENTS = 18  # Higher value to scrape more listings


# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for more details
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("boligportal_scraper.log"),  # Save to file
        logging.StreamHandler()  # Also logger.info to console
    ]
)

logger = logging.getLogger(__name__)

def send_telegram_notification(message, max_retries=3):
    """Send a Telegram message with retry logic."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}

    for attempt in range(max_retries):
        try:
            response = requests.post(url, data=data, timeout=10)
            if response.status_code == 200:
                logger.info("✅ Telegram message sent successfully.")
                return
            else:
                logger.info(f"⚠️ Telegram API responded with {response.status_code}: {response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Telegram message failed (Attempt {attempt+1}/{max_retries}): {e}")
        
        if attempt < max_retries - 1:
            time.sleep(2)  # Wait before retrying
    
    logger.info("🚨 Failed to send Telegram message after multiple attempts.")


async def log_in(page):
    try:
        await page.wait_for_selector("a.css-7334qx", timeout=5000)
        await page.click("a.css-7334qx")

        await page.fill('#__TextField21', BOLIGPORTAL_EMAIL)
        await page.fill('#__TextField23', BOLIGPORTAL_PASSWORD)

        await page.click('button[data-test-id="loginSubmit"]')
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
    except Exception as e:
        logger.info(f"Login failed: {e}")

async def navigate_safe(page, url, max_retries=3):
    """Try to navigate to a URL with retries in case of failure."""
    for attempt in range(max_retries):
        try:
            await page.goto(url, timeout=10000)
            logger.info(f"✅ Successfully navigated to {url}")
            return  # Exit function on success
        except Exception as e:
            logger.error(f"⚠️ Error navigating to {url} (Attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)  # Wait before retrying
    logger.info(f"🚨 Failed to navigate to {url} after {max_retries} attempts.")


async def close_popups(page):
    try:
        # Wait for one of the buttons to appear
        button_selectors = ["#declineButton", "button.css-176et4n"]
        button = None

        for selector in button_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=3000)
                if button:
                    logger.info(f"✅ Found popup button: {selector}")
                    break  # Stop loop once a button is found
            except:
                pass  # Continue to next selector if this one is not found

        if button:
            await button.click()
            logger.info("✅ Popup closed.")
            await asyncio.sleep(1)  # Allow UI transition
        else:
            logger.error("❌ No popup button found.")

    except Exception as e:
        logger.error(f"❌ Error closing popups: {e}")


async def get_total_listings(page):
    try:
        element = await page.query_selector(".css-1us17g7")  # Adjust selector
        text = await element.inner_text()
        return int(re.search(r'\d+', text).group()) if text else 0
    except:
        return 0

def load_contacted_links():
    """Load contacted links from file efficiently."""
    if Path(CONTACTED_FILE).exists():
        with open(CONTACTED_FILE, "r") as f:
            return set(line.strip() for line in f)  # Convert to set for fast lookup
    return set()

def save_new_links(new_links):
    """Save only new links to the contacted listings file."""
    contacted_links = load_contacted_links()  # Load existing links

    with open(CONTACTED_FILE, "a") as f:
        for link in new_links:
            if link not in contacted_links:  # Avoid writing duplicates
                f.write(link + "\n")

async def scrape_boligportal(page):
    hrefs = []  # Store all listing URLs
    count = 0  

    while count < MAX_NR_APARTMENTS:
        url = f"https://www.boligportal.dk/lejeboliger/k%C3%B8benhavn/5+-v%C3%A6relser/?max_monthly_rent=30000&offset={count}"
        await navigate_safe(page, url)

        try:
            await page.wait_for_selector(".AdCardSrp__Link", timeout=5000)
            elements = await page.query_selector_all(".AdCardSrp__Link")

            for element in elements:
                price_element = await element.query_selector('.css-dlcfcd')

                if price_element:  # Ensure element exists
                    price_text = await price_element.inner_text()  # Get price as text
                    price = int(price_text.replace("kr.", "").replace(".", "").strip())  # Convert to number
                    if price < MAX_PRICE:
                        href = await element.get_attribute("href")
                        if href:
                            hrefs.append(f"https://www.boligportal.dk{href}")

        except Exception as e:
            logger.info(f"⚠️ Error extracting listings: {e}")  # Don't stop script

        count += 18  # Move to next page

    unique_hrefs = list(set(hrefs))  # Remove duplicates
    contacted_links = load_contacted_links()
    new_links = [link for link in unique_hrefs if link not in contacted_links]
    save_new_links(new_links)

    return new_links

async def check_url_contains(page, keyword="indbakke"):
    """Check if the current URL contains a specific keyword."""
    await asyncio.sleep(2)  # Allow time for redirection if necessary
    current_url = page.url  # Get the current page URL
    if keyword in current_url:
        logger.info(f"✅ The URL contains '{keyword}': {current_url}")
        return True
    else:
        logger.info(f"❌ The URL does NOT contain '{keyword}': {current_url}")
        return False


async def send_messages(page, listings):
    for listing in listings:
        await navigate_safe(page, listing)

        # Click contact button
        await page.wait_for_selector("button.temporaryButtonClassname.css-1ly3ldq", timeout=5000)
        await page.click("button.temporaryButtonClassname.css-1ly3ldq")
        
        # Check if we already messaged the listing
        if not await check_url_contains(page):
            # If not then we contact them
            await page.fill('#__TextField1', MESSAGE_TEMPLATE)
            await page.locator("button:has-text('Send')").click()
        else:
            continue

    return

async def main():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            logger.info("🔍 Starting BoligPortal scraper...")

            # Navigate & close popups
            await navigate_safe(page, "https://www.boligportal.dk/lejeboliger/k%C3%B8benhavn/5+-v%C3%A6relser/?max_monthly_rent=30000")      
            await close_popups(page)

            listings = await scrape_boligportal(page)
            await close_popups(page)

            await log_in(page)

            await send_messages(page, listings)

            if len(listings) <= 0:
                send_telegram_notification("No new listings found.")
            else:
                send_telegram_notification(f"Found {len(listings)} new listings.")
                for count, listing in enumerate(listings, start=1):
                    send_telegram_notification(f"{count}. {listing}")

            logger.info("✅ Scraper completed successfully.")

    except Exception as e:
        logger.critical(f"🚨 Unexpected error in main(): {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
