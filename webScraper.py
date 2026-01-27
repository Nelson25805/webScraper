# webScraper.py
"""
Streamlit Web Scraper + Image Extractor
- Enter a URL
- Optionally provide a CSS selector to extract elements (default: "p")
- Optionally scrape images on the page
- Preview results and download CSV / images ZIP / image metadata Excel

Run:
- streamlit run webScraper.py
"""

from __future__ import annotations
import sys
import time
import csv
import io
import os
import zipfile
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

# optional pandas for prettier tables / Excel
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

    def extract_image_metadata(
        self,
        soup: BeautifulSoup,
        base_url: str,
        selector: Optional[str] = None,
        limit: Optional[int] = None,
        run_ocr: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Return metadata for images: url, filename, alt, title, caption (figcaption),
        parent_text, prev_sibling_text, next_sibling_text, container_text, ocr_text.
        """
        img_urls = self.extract_image_urls(
            soup, base_url, selector=selector, limit=limit
        )
        rows: List[Dict[str, Any]] = []

        # optional pytesseract OCR check
        ocr_available = False
        if run_ocr:
            try:
                import pytesseract  # type: ignore
                from PIL import Image  # type: ignore

                ocr_available = True
            except Exception:
                ocr_available = False

        for i, iu in enumerate(img_urls, start=1):
            img_tag = None
            if selector:
                for el in self.select(soup, selector, limit=None):
                    for img in el.find_all("img"):
                        src = img.get("src")
                        if src and urljoin(base_url, src) == iu:
                            img_tag = img
                            break
                    if img_tag:
                        break
            if not img_tag:
                for img in soup.find_all("img"):
                    src = img.get("src")
                    if src and urljoin(base_url, src) == iu:
                        img_tag = img
                        break

            parsed = urlparse(iu)
            fname = os.path.basename(parsed.path) or f"image_{i}.jpg"

            alt = img_tag.get("alt") if img_tag is not None else ""
            title = img_tag.get("title") if img_tag is not None else ""
            caption = ""
            parent_text = ""
            prev_text = ""
            next_text = ""
            container_text = ""

            if img_tag is not None:
                fig = img_tag.find_parent("figure")
                if fig:
                    fc = fig.find("figcaption")
                    if fc:
                        caption = fc.get_text(strip=True)
                parent = img_tag.find_parent()
                if parent:
                    parent_text = parent.get_text(" ", strip=True)
                    prev_sib = img_tag.find_previous_sibling()
                    if prev_sib:
                        prev_text = prev_sib.get_text(" ", strip=True)
                    next_sib = img_tag.find_next_sibling()
                    if next_sib:
                        next_text = next_sib.get_text(" ", strip=True)
                container = img_tag.find_parent(
                    ["div", "article", "section", "p", "main"]
                )
                if container:
                    container_text = container.get_text(" ", strip=True)

            ocr_text = ""
            if run_ocr and ocr_available:
                try:
                    b = self.http.fetch_bytes(iu)
                    if b:
                        from PIL import Image  # type: ignore
                        import io as _io

                        img_stream = _io.BytesIO(b)
                        im = Image.open(img_stream).convert("RGB")
                        import pytesseract  # type: ignore

                        ocr_text = pytesseract.image_to_string(im)
                except Exception:
                    ocr_text = ""

            rows.append(
                {
                    "index": i,
                    "image_url": iu,
                    "filename": fname,
                    "alt": alt or "",
                    "title": title or "",
                    "caption": caption or "",
                    "parent_text": parent_text or "",
                    "prev_sibling_text": prev_text or "",
                    "next_sibling_text": next_text or "",
                    "container_text": container_text or "",
                    "ocr_text": ocr_text or "",
                }
            )

        return rows


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


def rows_to_excel_bytes(rows: List[Dict[str, Any]]) -> bytes:
    """
    Convert rows to an Excel .xlsx in-memory. Uses pandas + openpyxl if available,
    otherwise falls back to CSV bytes.
    """
    if not rows:
        return b""
    if pd is None:
        return rows_to_csv_bytes(rows)
    try:
        buf = io.BytesIO()
        df = pd.DataFrame(rows)
        # Use openpyxl engine. Requires openpyxl installed.
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        return buf.read()
    except Exception:
        # fall back to CSV bytes if Excel writing fails
        return rows_to_csv_bytes(rows)


def images_to_zip_bytes(images: List[Dict[str, Any]]) -> bytes:
    # images: list of {"filename": ..., "data": bytes}
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for img in images:
            # ensure unique filenames by prefixing index if duplicates exist
            name = img.get("filename") or "image.bin"
            zf.writestr(name, img["data"])
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

                # Images + metadata extraction
                if scrape_images:
                    img_limit = max_images if max_images > 0 else None
                    run_ocr = st.sidebar.checkbox(
                        "Run OCR on images (slow)", value=False
                    )
                    st.write("Extracting image metadata...")
                    img_meta = scraper.extract_image_metadata(
                        soup,
                        url,
                        selector=selector or None,
                        limit=img_limit,
                        run_ocr=run_ocr,
                    )
                    st.write(f"Found {len(img_meta)} images with metadata.")

                    if img_meta:
                        if pd is not None:
                            df_imgs = pd.DataFrame(img_meta)
                            st.dataframe(df_imgs)

                            # Downloads: CSV and Excel
                            csv_bytes = rows_to_csv_bytes(img_meta)
                            st.download_button(
                                "Download image metadata CSV",
                                data=csv_bytes,
                                file_name="images_metadata.csv",
                                mime="text/csv",
                            )

                            excel_bytes = rows_to_excel_bytes(img_meta)
                            st.download_button(
                                "Download image metadata (Excel)",
                                data=excel_bytes,
                                file_name="images_metadata.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            )
                        else:
                            st.write(img_meta)
                            csv_bytes = rows_to_csv_bytes(img_meta)
                            st.download_button(
                                "Download image metadata CSV",
                                data=csv_bytes,
                                file_name="images_metadata.csv",
                                mime="text/csv",
                            )

                        # Download images as zip (optionally)
                        if st.button("Download images (zip)"):
                            images: List[Dict[str, Any]] = []
                            with st.spinner("Downloading images..."):
                                for i, im in enumerate(img_meta, 1):
                                    iu = im.get("image_url")
                                    data = scraper.http.fetch_bytes(iu) if iu else None
                                    if data:
                                        # create a safe filename using index + original name
                                        parsed = urlparse(iu)
                                        base = (
                                            os.path.basename(parsed.path)
                                            or f"image_{i}.bin"
                                        )
                                        safe_name = f"{i:03d}_{base}"
                                        images.append(
                                            {"filename": safe_name, "data": data}
                                        )
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
# Run the Streamlit UI
# --------------------------
if __name__ == "__main__":
    # When running with "streamlit run webScraper.py" Streamlit executes the file.
    # Call the UI function so the app renders.
    try:
        run_streamlit_ui()
    except Exception as e:
        import traceback

        traceback.print_exc()
        print("Failed to start Streamlit UI:", e)
