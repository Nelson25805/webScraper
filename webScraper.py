"""
Streamlit Web Scraper + Image Extractor
- Enter a URL
- Optionally provide a CSS selector to extract elements (default: "p")
- Optionally scrape images on the page
- Preview results and download CSV / images ZIP

Run:
- Press Run in VS Code (the wrapper will spawn Streamlit), or
- streamlit run webScraper.py
"""

from __future__ import annotations
import sys
import time
import csv
import io
import os
import zipfile
import tempfile
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

# optional pandas for prettier tables
try:
    import pandas as pd
except Exception:
    pd = None


# --------------------------
# Simple HttpClient
# --------------------------
class HttpClient:
    def __init__(
        self,
        user_agent: Optional[str] = None,
        max_retries: int = 2,
        backoff: float = 0.5,
    ):
        self.session = requests.Session()
        self.max_retries = max_retries
        self.backoff = backoff
        self.user_agent = user_agent or "Mozilla/5.0 (compatible; ScraperBot/1.0)"
        self.session.headers.update({"User-Agent": self.user_agent})

    def fetch_text(self, url: str, timeout: int = 10) -> Optional[str]:
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.session.get(url, timeout=timeout)
                r.raise_for_status()
                return r.text
            except requests.RequestException as e:
                last_exc = e
                time.sleep(self.backoff * attempt)
        print(f"[HttpClient] fetch failed {url}: {last_exc}")
        return None

    def fetch_bytes(self, url: str, timeout: int = 15) -> Optional[bytes]:
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.session.get(url, timeout=timeout)
                r.raise_for_status()
                return r.content
            except requests.RequestException as e:
                last_exc = e
                time.sleep(self.backoff * attempt)
        print(f"[HttpClient] fetch_bytes failed {url}: {last_exc}")
        return None


# --------------------------
# Scraper + Extractor
# --------------------------
class Scraper:
    def __init__(self, http: Optional[HttpClient] = None):
        self.http = http or HttpClient()

    def fetch_soup(
        self, url: str, parser: str = "html.parser"
    ) -> Optional[BeautifulSoup]:
        html = self.http.fetch_text(url)
        if html is None:
            return None
        return BeautifulSoup(html, parser)

    def select(
        self, soup: BeautifulSoup, selector: str, limit: Optional[int] = None
    ) -> List[Tag]:
        try:
            elements = soup.select(selector)
        except Exception:
            elements = []
        if limit and limit > 0:
            return elements[:limit]
        return elements

    def extract_elements(self, elements: List[Tag]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for el in elements:
            rows.append(
                {
                    "text": el.get_text(strip=True),
                    "html": str(el),
                    "attrs": dict(el.attrs),
                }
            )
        return rows

    def extract_image_urls(
        self,
        soup: BeautifulSoup,
        base_url: str,
        selector: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[str]:
        # If selector is provided, first select elements and look for <img> inside them.
        imgs = []
        if selector:
            els = self.select(soup, selector, limit=None)
            for el in els:
                for img in el.find_all("img"):
                    src = img.get("src")
                    if src:
                        imgs.append(urljoin(base_url, src))
        else:
            for img in soup.find_all("img"):
                src = img.get("src")
                if src:
                    imgs.append(urljoin(base_url, src))

        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for u in imgs:
            if u not in seen:
                seen.add(u)
                unique.append(u)

        if limit and limit > 0:
            return unique[:limit]
        return unique


# --------------------------
# Storage helpers
# --------------------------
def rows_to_csv_bytes(rows: List[Dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def images_to_zip_bytes(images: List[Dict[str, Any]]) -> bytes:
    # images: list of {"filename": ..., "data": bytes}
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for img in images:
            zf.writestr(img["filename"], img["data"])
    mem.seek(0)
    return mem.read()


# --------------------------
# Streamlit UI
# --------------------------
def run_streamlit_ui():
    import streamlit as st

    st.set_page_config(page_title="Web Scraper", layout="wide")
    st.title("Web Scraper + Image Extractor")

    st.sidebar.header("Options")
    url = st.sidebar.text_input("Website URL", value="https://example.com")
    selector = st.sidebar.text_input("CSS selector (empty = whole page)", value="p")
    limit = st.sidebar.number_input("Limit (0 = all)", min_value=0, value=10, step=1)
    scrape_images = st.sidebar.checkbox("Scrape images", value=True)
    max_images = st.sidebar.number_input(
        "Max images to download (0 = all)", min_value=0, value=10, step=1
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        "Notes:\n- This scraper uses requests+BeautifulSoup (no JS rendering).\n- For JS sites, consider Playwright or Selenium."
    )

    st.markdown(
        "Enter a URL and press **Scrape**. Use the selector to narrow results (e.g., `.post`, `article`, `div.product`)."
    )

    scraper = Scraper()

    if st.button("Scrape"):
        if not url:
            st.error("Please enter a URL.")
        else:
            with st.spinner("Fetching page..."):
                soup = scraper.fetch_soup(url)
            if soup is None:
                st.error("Failed to fetch page. Check URL or network.")
            else:
                # Extract elements
                real_limit = limit if limit > 0 else None
                elements = scraper.select(soup, selector) if selector else [soup]
                if real_limit:
                    elements = elements[:real_limit]

                rows = scraper.extract_elements(elements)
                st.success(
                    f"Found {len(rows)} elements (selector='{selector or 'whole page'}')."
                )

                # Show table (pandas if available)
                if pd is not None and rows:
                    df = pd.DataFrame(rows)
                    st.dataframe(df)
                    csv_bytes = rows_to_csv_bytes(rows)
                    st.download_button(
                        "Download CSV",
                        data=csv_bytes,
                        file_name="extracted.csv",
                        mime="text/csv",
                    )
                else:
                    st.write(rows)

                # Images
                if scrape_images:
                    img_limit = max_images if max_images > 0 else None
                    img_urls = scraper.extract_image_urls(
                        soup, url, selector=selector or None, limit=img_limit
                    )
                    st.write(f"Found {len(img_urls)} image URLs.")
                    if img_urls:
                        # Download images (first N)
                        images: List[Dict[str, Any]] = []
                        with st.spinner("Downloading images..."):
                            for i, iu in enumerate(img_urls, 1):
                                data = scraper.http.fetch_bytes(iu)
                                if data:
                                    # pick base filename from URL
                                    parsed = urlparse(iu)
                                    fname = (
                                        os.path.basename(parsed.path)
                                        or f"image_{i}.jpg"
                                    )
                                    # sanitize names if needed
                                    images.append({"filename": fname, "data": data})
                                else:
                                    print(f"failed to download {iu}")

                        # Display first few images
                        max_preview = 10
                        preview = images[:max_preview]
                        if preview:
                            st.write("Image preview (first images):")
                            st.image([img["data"] for img in preview], width=200)

                        # Offer zip download
                        if images:
                            zip_bytes = images_to_zip_bytes(images)
                            st.download_button(
                                "Download images (zip)",
                                data=zip_bytes,
                                file_name="images.zip",
                                mime="application/zip",
                            )
                        else:
                            st.warning("No images downloaded successfully.")

    # small footer
    st.sidebar.markdown("---")
    st.sidebar.markdown("Respect website terms & robots.txt. Use responsibly.")


# --------------------------
# Friendly 'Run' wrapper for VS Code Run ▶
# --------------------------
if __name__ == "__main__":
    # If the script is run directly (e.g., pressing Run ▶ in VS Code) we spawn Streamlit,
    # which opens a browser UI. If the file is launched with CLI args, you can extend
    # this path to support CLI mode too.
    import shutil
    import subprocess

    # If args were given, you could parse them for CLI mode. Here we simply detect
    # no args -> spawn Streamlit for the UI.
    if len(sys.argv) == 1:
        streamlit_bin = shutil.which("streamlit")
        if streamlit_bin:
            # Spawn streamlit in a separate process so the Run control returns immediately.
            subprocess.Popen([sys.executable, "-m", "streamlit", "run", __file__])
            print(
                "Streamlit started. Check the terminal output in VS Code or your browser at http://localhost:8501"
            )
        else:
            print("Streamlit not found. Run: python -m pip install streamlit")
    else:
        # If you run with arguments in the future, handle CLI flow here.
        print("This script currently launches the Streamlit UI when run without args.")
