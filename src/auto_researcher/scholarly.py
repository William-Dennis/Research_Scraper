from pathlib import Path
import re
import time
import requests
import json
from scholarly import scholarly
import arxiv
import logging
from urllib.error import HTTPError
from datetime import datetime, timezone

from .helpers import timeout

DELAY = 2  # in seconds


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s | %(levelname)s | %(message)s",
    filename="errors.log",  # errors + tracebacks go here
)


ARXIV_FAIL_DIR = Path(".cache_arxiv_failed")


def clean_filename(s: str, max_len: int = 150) -> str:
    s = re.sub(r'[<>:"/\\|?*]', "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def count_pdfs_in_folder(folder: Path) -> int:
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return 0
    return sum(1 for f in folder.glob("*.pdf") if f.is_file())


def arxiv_fail_record(
    title: str, *, query=None, arxiv_id=None, pdf_url=None, reason=None
):
    ARXIV_FAIL_DIR.mkdir(parents=True, exist_ok=True)

    record = {
        "title": title,
        "query": query,
        "arxiv_id": arxiv_id,
        "pdf_url": pdf_url,
        "reason": reason,
        "timestamp": utc_now_iso(),
    }

    key = clean_filename(title).replace(" ", "_")
    cache_file = ARXIV_FAIL_DIR / f"{key}.json"

    with cache_file.open("w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)


@timeout()
def arxiv_search_or_fail(title: str):
    key = clean_filename(title).replace(" ", "_")
    cache_file = ARXIV_FAIL_DIR / f"{key}.json"

    # ---- known failure ----
    if cache_file.exists():
        return None

    query = f'ti:"{title}"'
    search = arxiv.Search(query=query, max_results=1)

    result = next(search.results(), None)

    if result is None:
        arxiv_fail_record(
            title,
            query=query,
            reason="no_search_results",
        )
        return None

    return result


@timeout()
def scholarly_fill_cached(pub, cache_dir=Path(".cache_fill")):
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Use a stable cache key from publication title + year if available
    title = pub.get("bib", {}).get("title", None)
    year = pub.get("bib", {}).get("year", "")
    key_base = f"{title}_{year}" if title else str(pub)
    key = clean_filename(key_base).replace(" ", "_")

    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists():
        with cache_file.open("r", encoding="utf-8") as f:
            cached = json.load(f)
        return cached

    time.sleep(DELAY)
    filled = scholarly.fill(pub)
    with cache_file.open("w", encoding="utf-8") as f:
        json.dump(filled, f, ensure_ascii=False, indent=2)
    return filled


@timeout()
def scholarly_search_by_author_id_cached(author_id, cache_dir=Path(".cache_author")):
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = clean_filename(author_id).replace(" ", "_")
    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists():
        with cache_file.open("r", encoding="utf-8") as f:
            cached = json.load(f)
        return cached

    time.sleep(DELAY / 2)
    author_obj = scholarly.search_author_id(author_id)
    time.sleep(DELAY / 2)
    author = scholarly.fill(author_obj)  # fill author details here for caching
    with cache_file.open("w", encoding="utf-8") as f:
        json.dump(author, f, ensure_ascii=False, indent=2)
    return author


def download_scholar_papers_by_id(
    scholar_user_id: str,
    max_downloads: int = 3,
    save_dir: str = "Papers",
    min_year: int = 2020,
):
    base_dir = Path(save_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    print("Searching.....")
    author = scholarly_search_by_author_id_cached(scholar_user_id)
    print("Found", author.get("name", "Unknown"))

    # Publications may not be fully loaded, so try to cache them separately
    pub_cache_file = (
        Path(".cache_author")
        / f"{clean_filename(scholar_user_id).replace(' ', '_')}_publications.json"
    )
    pub_cache_file.parent.mkdir(parents=True, exist_ok=True)

    if pub_cache_file.exists():
        with pub_cache_file.open("r", encoding="utf-8") as f:
            author["publications"] = json.load(f)
    else:
        # Load publications from scholarly.fill with sections param
        # Here, author is a dict, so we simulate fill by getting publications one by one
        time.sleep(DELAY / 2)
        author_obj = scholarly.search_author_id(scholar_user_id)
        time.sleep(DELAY / 2)
        author_filled = scholarly.fill(author_obj, sections=["publications"])
        author["publications"] = author_filled.get("publications", [])
        with pub_cache_file.open("w", encoding="utf-8") as f:
            json.dump(author["publications"], f, ensure_ascii=False, indent=2)

    author_name = clean_filename(author.get("name", "unknown_author"))
    out_dir = base_dir / author_name
    out_dir.mkdir(exist_ok=True)

    pdf_count = count_pdfs_in_folder(out_dir)
    if pdf_count >= max_downloads:
        print(f"Already have {pdf_count} PDFs, skipping download.")
        return

    publications = author.get("publications", [])

    def pub_year(pub):
        default = min_year - 1
        try:
            return int(pub.get("bib", {}).get("year", default))
        except Exception:
            return default

    # Sort descending by year
    publications = sorted(publications, key=pub_year, reverse=True)

    print(f"Author: {author_name}")
    print(f"Publications: {len(publications)}\n")

    downloaded, skipped, failed = 0, 0, 0

    for i, pub in enumerate(publications, 1):
        if downloaded >= max_downloads:
            break

        title = pub.get("bib", {}).get("title", f"paper_{i}")
        filename = clean_filename(title) + ".pdf"
        pdf_path = out_dir / filename

        print(f"[{i}/{len(publications)}] {title}")

        if pdf_path.exists():
            print("  ⊙ exists, skipping")
            downloaded += 1
            skipped += 1
            continue

        # ---- 1. Fill publication metadata ----
        try:
            pub_filled = scholarly_fill_cached(pub)
        except Exception:
            print("  ✗ error while filling publication metadata")
            logging.exception("Fill failed for: %s", title)
            # for now we will raise an exception since we are in development
            raise ConnectionRefusedError(
                "Scholarly Fill Failed. Likely cause is a connection error.\nCheck Logs"
            )
            failed += 1
            continue

        # ---- 2. Try direct PDF download ----
        try:
            pdf_url = pub_filled.get("eprint_url") or pub_filled.get("pub_url")

            if pdf_url and ".pdf" in pdf_url.lower():
                r = requests.get(
                    pdf_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=30,
                )

                if r.status_code == 200 and "application/pdf" in r.headers.get(
                    "Content-Type", ""
                ):
                    pdf_path.write_bytes(r.content)
                    print("  ✓ downloaded (direct)")
                    downloaded += 1
                    continue

        except Exception:
            print("  ✗ error during direct PDF download")
            logging.exception("scholarly_fill_cached failed: %s", title)

        # ---- 3. Fallback to arXiv ----
        try:
            arxiv_result = arxiv_search_or_fail(title)

            if arxiv_result is None:
                print("  ✗ not found (cached arXiv failure)")
                failed += 1
            else:
                try:
                    arxiv_result.download_pdf(dirpath=out_dir, filename=filename)
                    print("  ✓ downloaded (arXiv)")
                    downloaded += 1
                except HTTPError as e:
                    arxiv_fail_record(
                        title,
                        query=f'ti:"{title}"',
                        arxiv_id=arxiv_result.get_short_id(),
                        pdf_url=arxiv_result.pdf_url,
                        reason=f"http_{e.code}",
                    )
                    print("  ✗ arXiv PDF failed")
                    failed += 1

        except Exception:
            logging.exception("Unexpected arXiv failure: %s", title)
            failed += 1

    print("\nSummary")
    print("-" * 40)
    print(f"Downloaded: {downloaded}")
    print(f"Skipped:    {skipped}")
    print(f"Failed:     {failed}")
    print(f"Saved to:   {out_dir.resolve()}")
