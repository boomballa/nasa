#!/usr/bin/env python3
"""
NASA APOD Batch Downloader
Downloads Astronomy Picture of the Day images with full metadata.
"""

import asyncio
import aiohttp
import aiofiles
import sqlite3
import json
import argparse
import os
import sys
import ssl
import logging
import certifi
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from tqdm.asyncio import tqdm as async_tqdm
from tqdm import tqdm

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_KEY       = os.getenv("NASA_API_KEY", "DEMO_KEY")
APOD_API_URL  = "https://api.nasa.gov/planetary/apod"
NASA_PAGE_BASE = "https://apod.nasa.gov/apod"

DATA_DIR   = Path("data")
IMAGES_DIR = DATA_DIR / "images"
DB_PATH    = DATA_DIR / "apod.db"

CONCURRENT_LIMIT = 5      # parallel downloads
REQUEST_DELAY    = 0.3    # seconds between API calls (respect rate limits)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS apod (
            date         TEXT PRIMARY KEY,
            title        TEXT,
            explanation  TEXT,
            media_type   TEXT,
            url          TEXT,
            hdurl        TEXT,
            nasa_page    TEXT,
            copyright    TEXT,
            local_path   TEXT,
            downloaded   INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON apod(date)")
    conn.commit()
    return conn


def upsert_record(conn: sqlite3.Connection, record: dict):
    conn.execute("""
        INSERT INTO apod (date, title, explanation, media_type, url, hdurl,
                          nasa_page, copyright, local_path, downloaded)
        VALUES (:date, :title, :explanation, :media_type, :url, :hdurl,
                :nasa_page, :copyright, :local_path, :downloaded)
        ON CONFLICT(date) DO UPDATE SET
            title       = excluded.title,
            explanation = excluded.explanation,
            media_type  = excluded.media_type,
            url         = excluded.url,
            hdurl       = excluded.hdurl,
            nasa_page   = excluded.nasa_page,
            copyright   = excluded.copyright,
            local_path  = excluded.local_path,
            downloaded  = excluded.downloaded
    """, record)
    conn.commit()


def already_downloaded(conn: sqlite3.Connection, apod_date: str) -> bool:
    row = conn.execute(
        "SELECT downloaded, local_path FROM apod WHERE date = ?", (apod_date,)
    ).fetchone()
    if not row:
        return False
    downloaded, local_path = row
    if downloaded and local_path and Path(local_path).exists():
        return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def nasa_page_url(apod_date: str) -> str:
    """Build the official NASA APOD page URL for a given date (YYYY-MM-DD)."""
    d = datetime.strptime(apod_date, "%Y-%m-%d")
    return f"{NASA_PAGE_BASE}/ap{d.strftime('%y%m%d')}.html"


def image_save_path(apod_date: str, ext: str) -> Path:
    """data/images/YYYY/MM/YYYY-MM-DD.ext"""
    d = datetime.strptime(apod_date, "%Y-%m-%d")
    folder = IMAGES_DIR / f"{d.year:04d}" / f"{d.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{apod_date}{ext}"


def meta_save_path(apod_date: str) -> Path:
    d = datetime.strptime(apod_date, "%Y-%m-%d")
    folder = IMAGES_DIR / f"{d.year:04d}" / f"{d.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{apod_date}.json"


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def render_markdown(meta: dict) -> str:
    title       = meta.get("title", "Untitled")
    date_str    = meta.get("date", "")
    explanation = meta.get("explanation", "")
    copyright_  = meta.get("copyright", "")
    nasa_page   = meta.get("nasa_page", "")
    media_type  = meta.get("media_type", "image")
    hdurl       = meta.get("hdurl", "")
    local_path  = meta.get("local_path")

    lines = [f"# {title}", ""]
    lines += [f"**Date:** {date_str}  "]
    if copyright_:
        lines += [f"**Copyright:** {copyright_}  "]
    lines += [""]

    # Embed image (prefer local path, fall back to remote URL)
    if media_type == "image":
        img_src = local_path if local_path else hdurl
        if img_src:
            lines += [f"![{title}]({img_src})", ""]
    elif media_type == "video":
        url = meta.get("url", "")
        if url:
            lines += [f"> **Video:** [{url}]({url})", ""]
        thumb = local_path
        if thumb:
            lines += [f"![thumbnail]({thumb})", ""]

    lines += ["## Explanation", ""]
    lines += [explanation, ""]
    lines += ["---", ""]
    if nasa_page:
        lines += [f"[View on NASA APOD]({nasa_page})"]

    return "\n".join(lines) + "\n"


def rebuild_markdown_from_db(conn: sqlite3.Connection):
    """Generate/overwrite .md files for every record already in the DB."""
    rows = conn.execute(
        "SELECT date, title, explanation, media_type, url, hdurl, nasa_page, copyright, local_path FROM apod"
    ).fetchall()
    cols = ["date", "title", "explanation", "media_type", "url", "hdurl", "nasa_page", "copyright", "local_path"]
    updated = 0
    for row in tqdm(rows, desc="Rebuilding markdown", unit="file"):
        meta = dict(zip(cols, row))
        md_path = meta_save_path(meta["date"]).with_suffix(".md")
        md_path.write_text(render_markdown(meta), encoding="utf-8")
        updated += 1
    print(f"Done. {updated} markdown files written.")


# ---------------------------------------------------------------------------
# HTML Gallery
# ---------------------------------------------------------------------------
_GALLERY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NASA APOD Gallery</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0a0a0f; color: #e0e0e0; font-family: system-ui, sans-serif; }

  /* ── Header ── */
  header { padding: 2rem 1.5rem 1rem; text-align: center; }
  header h1 { font-size: 1.6rem; letter-spacing: .08em; color: #fff; }
  header p  { margin-top: .4rem; font-size: .85rem; color: #888; }

  /* ── Year filter ── */
  #filters {
    display: flex; flex-wrap: wrap; justify-content: center;
    gap: .5rem; padding: .8rem 1rem 1.2rem;
  }
  #filters button {
    background: #1c1c28; border: 1px solid #333; color: #aaa;
    padding: .35rem .9rem; border-radius: 999px; cursor: pointer;
    font-size: .8rem; transition: all .15s;
  }
  #filters button:hover  { border-color: #888; color: #fff; }
  #filters button.active { background: #2563eb; border-color: #2563eb; color: #fff; }

  /* ── Search ── */
  #search-wrap { text-align: center; margin-bottom: 1.2rem; }
  #search {
    background: #1c1c28; border: 1px solid #333; color: #e0e0e0;
    padding: .45rem 1rem; border-radius: 999px; width: min(340px, 90%);
    font-size: .85rem; outline: none;
  }
  #search:focus { border-color: #2563eb; }

  /* ── Grid ── */
  #gallery-container { padding: 0 3px 3px; }
  .month-group { margin-bottom: 2rem; }
  .month-header {
    font-size: 1.4rem; color: #fff; padding: 1rem 0.5rem 0.5rem;
    border-bottom: 1px solid #333; margin-bottom: 0.8rem;
    font-weight: 600; letter-spacing: 0.05em;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 3px;
  }
  .card {
    position: relative; aspect-ratio: 1; overflow: hidden; cursor: pointer;
    background: #111;
  }
  .card img {
    width: 100%; height: 100%; object-fit: cover;
    transition: transform .3s ease, filter .3s ease;
    filter: brightness(.85);
  }
  .card.is-read img { filter: brightness(0.4) grayscale(50%); opacity: 0.6; }
  .card.is-read::before {
    content: "已读"; position: absolute; top: 6px; right: 6px;
    background: rgba(37, 99, 235, 0.8); color: #fff; font-size: 0.65rem; padding: 2px 5px; border-radius: 4px; z-index: 2;
  }
  .card:hover img { transform: scale(1.06); filter: brightness(1); }
  .card .overlay {
    position: absolute; inset: 0;
    background: linear-gradient(to top, rgba(0,0,0,.75) 0%, transparent 55%);
    opacity: 0; transition: opacity .25s;
    display: flex; flex-direction: column; justify-content: flex-end;
    padding: .7rem;
  }
  .card:hover .overlay { opacity: 1; }
  .card .overlay .date  { font-size: .7rem; color: #aaa; }
  .card .overlay .title { font-size: .85rem; color: #fff; font-weight: 600; margin-top: .15rem; line-height: 1.25; }
  .card.video-card::after {
    content: "▶"; position: absolute; top: 50%; left: 50%;
    transform: translate(-50%,-50%);
    font-size: 2rem; color: rgba(255,255,255,.7); pointer-events: none;
  }
  #empty { text-align: center; color: #555; padding: 4rem; display: none; }

  /* ── Modal ── */
  #modal {
    display: none; position: fixed; inset: 0; z-index: 100;
    background: rgba(0,0,0,.92); overflow-y: auto;
    padding: 2rem 1rem 3rem;
  }
  #modal.open { display: flex; align-items: flex-start; justify-content: center; }
  #modal-inner {
    position: relative; max-width: 900px; width: 100%;
    background: #12121c; border-radius: 12px; overflow: hidden;
  }
  #modal-close {
    position: absolute; top: .8rem; right: .8rem; z-index: 10;
    background: rgba(0,0,0,.6); border: none; color: #fff;
    font-size: 1.4rem; width: 2.2rem; height: 2.2rem;
    border-radius: 50%; cursor: pointer; line-height: 1;
  }
  #modal img {
    width: 100%; max-height: 70vh; object-fit: contain;
    background: #000; display: block;
  }
  #modal .video-embed { padding: 1rem; color: #aaa; font-size: .9rem; }
  #modal .video-embed a { color: #60a5fa; }
  #modal-body { padding: 1.4rem 1.6rem 1.8rem; }
  #modal-date  { font-size: .75rem; color: #666; margin-bottom: .3rem; }
  #modal-title { font-size: 1.3rem; font-weight: 700; color: #fff; margin-bottom: .25rem; }
  #modal-copy  { font-size: .75rem; color: #888; margin-bottom: .9rem; }
  #modal-expl  { font-size: .88rem; line-height: 1.7; color: #ccc; }
  #modal-links { margin-top: 1.2rem; display: flex; gap: 1rem; flex-wrap: wrap; }
  #modal-links a {
    font-size: .8rem; color: #60a5fa; text-decoration: none;
    border: 1px solid #1e3a5f; padding: .3rem .8rem; border-radius: 6px;
  }
  #modal-links a:hover { background: #1e3a5f; }

  /* ── Stats bar ── */
  #stats { text-align: center; padding: .6rem; font-size: .75rem; color: #555; }
</style>
</head>
<body>

<header>
  <h1>NASA · Astronomy Picture of the Day</h1>
  <p id="total-count"></p>
</header>

<div id="filters">
  <button class="active" data-year="all">All</button>YEAR_BUTTONS
  <div style="width:1px; background:#444; margin:0 .5rem;"></div>
  <button id="toggle-unread">只看未读</button>
</div>

<div id="search-wrap">
  <input id="search" type="search" placeholder="Search by title or date…">
</div>

<div id="gallery-container">CARDS</div>
<div id="empty">No results found.</div>
<div id="stats"><span id="shown-count"></span></div>

<!-- Modal -->
<div id="modal">
  <div id="modal-inner">
    <button id="modal-close" aria-label="Close">✕</button>
    <div id="modal-media"></div>
    <div id="modal-body">
      <div id="modal-date"></div>
      <div id="modal-title"></div>
      <div id="modal-copy"></div>
      <div id="modal-expl"></div>
      <div id="modal-links"></div>
    </div>
  </div>
</div>

<script>
const DATA = JSON_DATA;

// Build lookup
const byDate = {};
DATA.forEach(d => byDate[d.date] = d);

const gallery    = document.getElementById('gallery-container');
const monthGroups= Array.from(gallery.querySelectorAll('.month-group'));
const emptyMsg   = document.getElementById('empty');
const shownEl    = document.getElementById('shown-count');
const totalEl    = document.getElementById('total-count');
const searchEl   = document.getElementById('search');

totalEl.textContent = DATA.length + ' images in archive';

let activeYear = 'all';
let searchQ    = '';
let showUnreadOnly = false;

// LocalStorage Read History
let readApods = JSON.parse(localStorage.getItem('apod_read_history') || '[]');
const readSet = new Set(readApods);
const toggleUnreadBtn = document.getElementById('toggle-unread');

toggleUnreadBtn.addEventListener('click', () => {
  showUnreadOnly = !showUnreadOnly;
  toggleUnreadBtn.classList.toggle('active', showUnreadOnly);
  applyFilters();
});

function applyFilters() {
  let totalShown = 0;
  monthGroups.forEach(group => {
    let groupShown = 0;
    const cards = Array.from(group.querySelectorAll('.card'));
    cards.forEach(card => {
      const year  = card.dataset.year;
      const text  = card.dataset.search;
      const isRead= readSet.has(card.dataset.date);
      
      const yOk   = activeYear === 'all' || year === activeYear;
      const sOk   = !searchQ || text.includes(searchQ);
      const unOk  = !showUnreadOnly || !isRead;
      const vis   = yOk && sOk && unOk;
      
      card.style.display = vis ? '' : 'none';
      if (isRead) card.classList.add('is-read');
      else card.classList.remove('is-read');

      if (vis) {
        groupShown++;
        totalShown++;
      }
    });
    group.style.display = groupShown > 0 ? '' : 'none';
  });
  emptyMsg.style.display = totalShown === 0 ? 'block' : 'none';
  shownEl.textContent = totalShown + ' / ' + DATA.length + ' shown';
}

// Year filter
document.getElementById('filters').addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (!btn || btn.id === 'toggle-unread') return;
  document.querySelectorAll('#filters button:not(#toggle-unread)').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeYear = btn.dataset.year;
  applyFilters();
});

// Search
searchEl.addEventListener('input', () => {
  searchQ = searchEl.value.toLowerCase().trim();
  applyFilters();
});

// Modal
const modal      = document.getElementById('modal');
const modalMedia = document.getElementById('modal-media');
const modalDate  = document.getElementById('modal-date');
const modalTitle = document.getElementById('modal-title');
const modalCopy  = document.getElementById('modal-copy');
const modalExpl  = document.getElementById('modal-expl');
const modalLinks = document.getElementById('modal-links');

function openModal(date) {
  if (!readSet.has(date)) {
    readSet.add(date);
    readApods.push(date);
    localStorage.setItem('apod_read_history', JSON.stringify(readApods));
    applyFilters(); // highlight card as read
  }

  const d = byDate[date];
  if (!d) return;
  modalDate.textContent  = d.date;
  modalTitle.textContent = d.title;
  modalCopy.textContent  = d.copyright ? '© ' + d.copyright : '';
  modalExpl.textContent  = d.explanation;

  modalLinks.innerHTML = '';
  if (d.nasa_page) {
    const a = document.createElement('a');
    a.href = d.nasa_page; a.target = '_blank'; a.textContent = 'NASA APOD Page';
    modalLinks.appendChild(a);
  }
  if (d.hdurl) {
    const a = document.createElement('a');
    a.href = d.hdurl; a.target = '_blank'; a.textContent = 'Original HD Image';
    modalLinks.appendChild(a);
  }

  modalMedia.innerHTML = '';
  if (d.local_path) {
    const img = document.createElement('img');
    img.src = d.local_path; img.alt = d.title;
    modalMedia.appendChild(img);
  } else if (d.media_type === 'video') {
    const div = document.createElement('div');
    div.className = 'video-embed';
    div.innerHTML = 'Video: <a href="' + d.url + '" target="_blank">' + d.url + '</a>';
    modalMedia.appendChild(div);
  }

  modal.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  modal.classList.remove('open');
  document.body.style.overflow = '';
}

gallery.addEventListener('click', e => {
  const card = e.target.closest('.card');
  if (card) openModal(card.dataset.date);
});
document.getElementById('modal-close').addEventListener('click', closeModal);
modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

applyFilters();
</script>
</body>
</html>
"""


def build_gallery(conn: sqlite3.Connection):
    rows = conn.execute("""
        SELECT date, title, explanation, media_type, url, hdurl,
               nasa_page, copyright, local_path
        FROM apod
        ORDER BY date DESC
    """).fetchall()

    if not rows:
        print("No records in database. Run a download first.")
        return

    cols = ["date", "title", "explanation", "media_type", "url", "hdurl",
            "nasa_page", "copyright", "local_path"]
    records = [dict(zip(cols, r)) for r in rows]

    # Collect unique years for filter buttons
    years = sorted({r["date"][:4] for r in records}, reverse=True)
    year_buttons = "".join(
        f'<button data-year="{y}">{y}</button>' for y in years
    )

    # Build card HTML for each record
    import datetime
    card_parts = []
    current_month = ""
    
    for r in records:
        year  = r["date"][:4]
        year_month = r["date"][:7]
        
        if year_month != current_month:
            if current_month != "":
                card_parts.append('</div></div>') # Close previous grid and month-group
            
            dt = datetime.datetime.strptime(year_month, "%Y-%m")
            month_name = dt.strftime("%B %Y") # e.g. 'April 2026'
            
            card_parts.append(f'<div class="month-group" data-year="{year}">')
            card_parts.append(f'<div class="month-header">{month_name}</div>')
            card_parts.append('<div class="grid">')
            current_month = year_month

        title = r["title"].replace('"', "&quot;").replace("<", "&lt;")
        date_ = r["date"]
        search_text = f"{date_} {r['title']}".lower().replace('"', "")

        # Thumbnail: local file (relative path from data/) or remote url
        local = r["local_path"]
        if local and local.startswith("data/"):
            local = local[5:]
            r["local_path"] = local  # Update it so JSON data also has the correct relative path
        
        if local:
            thumb_src = local
        elif r["media_type"] == "image":
            thumb_src = r["url"]
        else:
            thumb_src = ""  # video without thumbnail

        video_class = " video-card" if r["media_type"] == "video" and not local else ""

        if thumb_src:
            img_tag = f'<img src="{thumb_src}" alt="{title}" loading="lazy">'
        else:
            img_tag = '<div style="width:100%;height:100%;background:#1a1a2e;display:flex;align-items:center;justify-content:center;color:#444;font-size:.75rem;">No image</div>'

        card_parts.append(
            f'<div class="card{video_class}" data-date="{date_}" '
            f'data-year="{year}" data-search="{search_text}">'
            f'{img_tag}'
            f'<div class="overlay">'
            f'<div class="date">{date_}</div>'
            f'<div class="title">{title}</div>'
            f'</div></div>'
        )

    if records:
        card_parts.append('</div></div>') # Close final grid and month-group

    # Serialize records to JSON for the JS data array
    js_data = json.dumps(records, ensure_ascii=False)

    html = _GALLERY_HTML
    html = html.replace("YEAR_BUTTONS", year_buttons)
    html = html.replace("CARDS", "\n".join(card_parts))
    html = html.replace("JSON_DATA", js_data)

    out_path = DATA_DIR / "gallery.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Gallery generated: {out_path.resolve()}")
    print(f"  {len(records)} images  |  {len(years)} years")
    print(f"  Open in browser: open {out_path}")


# ---------------------------------------------------------------------------
# Core fetch + download
# ---------------------------------------------------------------------------

# Global rate-limit gate: when one worker hits 429, all workers wait here.
_rate_limit_until: float = 0.0


async def fetch_metadata(session: aiohttp.ClientSession, apod_date: str, _retries: int = 3) -> Optional[dict]:
    global _rate_limit_until

    # Honour any active global backoff before sending the request
    wait = _rate_limit_until - asyncio.get_event_loop().time()
    if wait > 0:
        await asyncio.sleep(wait)

    params = {"api_key": API_KEY, "date": apod_date, "thumbs": "true"}
    try:
        async with session.get(APOD_API_URL, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 429:
                backoff = 65
                log.warning(f"Rate limited — pausing all workers for {backoff}s...")
                _rate_limit_until = asyncio.get_event_loop().time() + backoff
                await asyncio.sleep(backoff)
                return await fetch_metadata(session, apod_date, _retries)
            elif resp.status in (500, 502, 503, 504) and _retries > 0:
                log.warning(f"API {resp.status} for {apod_date}, retrying in 5s... ({_retries} left)")
                await asyncio.sleep(5)
                return await fetch_metadata(session, apod_date, _retries - 1)
            else:
                log.error(f"API error {resp.status} for {apod_date}")
                return None
    except Exception as e:
        log.error(f"Failed to fetch metadata for {apod_date}: {e}")
        return None


async def download_image(session: aiohttp.ClientSession, url: str, save_path: Path) -> bool:
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                async with aiofiles.open(save_path, "wb") as f:
                    await f.write(await resp.read())
                return True
            else:
                log.error(f"Image download failed {resp.status}: {url}")
                return False
    except Exception as e:
        log.error(f"Image download exception: {e}")
        return False


async def process_one(
    session: aiohttp.ClientSession,
    conn: sqlite3.Connection,
    apod_date: str,
    semaphore: asyncio.Semaphore,
    skip_existing: bool = True,
) -> dict:
    """Fetch metadata + download image for one date. Returns a status dict."""
    async with semaphore:
        await asyncio.sleep(REQUEST_DELAY)

        if skip_existing and already_downloaded(conn, apod_date):
            return {"date": apod_date, "status": "skipped"}

        metadata = await fetch_metadata(session, apod_date)
        if not metadata:
            return {"date": apod_date, "status": "error", "reason": "metadata fetch failed"}

        media_type = metadata.get("media_type", "image")
        title      = metadata.get("title", "")
        explanation = metadata.get("explanation", "")
        url        = metadata.get("url", "")
        hdurl      = metadata.get("hdurl") or url   # fall back to url if no HD
        copyright_ = metadata.get("copyright", "")
        nasa_page  = nasa_page_url(apod_date)

        record = {
            "date":        apod_date,
            "title":       title,
            "explanation": explanation,
            "media_type":  media_type,
            "url":         url,
            "hdurl":       hdurl,
            "nasa_page":   nasa_page,
            "copyright":   copyright_,
            "local_path":  None,
            "downloaded":  0,
        }

        # Only download actual images (skip YouTube videos etc.)
        if media_type == "image":
            # Determine extension from URL
            ext = Path(hdurl.split("?")[0]).suffix or ".jpg"
            img_path = image_save_path(apod_date, ext)
            success  = await download_image(session, hdurl, img_path)

            if success:
                record["local_path"] = str(img_path)
                record["downloaded"] = 1
            else:
                record["downloaded"] = 0
        else:
            # For videos, download the thumbnail image if provided by API
            thumbnail_url = metadata.get("thumbnail_url")
            if thumbnail_url:
                ext = Path(thumbnail_url.split("?")[0]).suffix or ".jpg"
                thumb_path = image_save_path(apod_date, ext)
                success = await download_image(session, thumbnail_url, thumb_path)
                if success:
                    record["local_path"] = str(thumb_path)
                    record["downloaded"] = 1

        # Save JSON sidecar
        meta = {
            "date":        apod_date,
            "title":       title,
            "explanation": explanation,
            "media_type":  media_type,
            "url":         url,
            "hdurl":       hdurl,
            "nasa_page":   nasa_page,
            "copyright":   copyright_,
            "local_path":  record.get("local_path"),
        }
        meta_path = meta_save_path(apod_date)
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))

        # Save Markdown sidecar
        md_path = meta_path.with_suffix(".md")
        async with aiofiles.open(md_path, "w", encoding="utf-8") as f:
            await f.write(render_markdown(meta))

        upsert_record(conn, record)
        status = "ok" if record["downloaded"] else f"no_image ({media_type})"
        return {"date": apod_date, "status": status}


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
async def run_batch(dates: list[str], skip_existing: bool = True):
    conn = init_db()
    semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)

    ssl_ctx   = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(limit=CONCURRENT_LIMIT, ssl=ssl_ctx)
    timeout   = aiohttp.ClientTimeout(total=60)

    results = {"ok": 0, "skipped": 0, "error": 0, "no_image": 0}

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [
            process_one(session, conn, d, semaphore, skip_existing)
            for d in dates
        ]
        for coro in async_tqdm(
            asyncio.as_completed(tasks),
            total=len(tasks),
            desc="Downloading",
            unit="apod",
        ):
            result = await coro
            s = result["status"]
            if s == "ok":
                results["ok"] += 1
            elif s == "skipped":
                results["skipped"] += 1
            elif s.startswith("no_image"):
                results["no_image"] += 1
                log.info(f"  {result['date']} — {s}")
            else:
                results["error"] += 1
                log.warning(f"  {result['date']} — {s}: {result.get('reason','')}")

    conn.close()
    print(
        f"\nDone. "
        f"downloaded={results['ok']}  "
        f"skipped={results['skipped']}  "
        f"no_image={results['no_image']}  "
        f"error={results['error']}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="NASA APOD Batch Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download today
  python downloader.py --today

  # Download a specific date
  python downloader.py --date 2024-04-16

  # Download a date range
  python downloader.py --start 2024-01-01 --end 2024-12-31

  # Download latest N days
  python downloader.py --latest 30

  # Re-download even if already exists
  python downloader.py --start 2024-01-01 --end 2024-01-31 --force
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--today",            action="store_true", help="Download today's APOD")
    group.add_argument("--date",             metavar="YYYY-MM-DD", help="Download a specific date")
    group.add_argument("--latest",           metavar="N", type=int, help="Download latest N days")
    group.add_argument("--start",            metavar="YYYY-MM-DD", help="Start date for range download")
    group.add_argument("--rebuild-markdown", action="store_true",
                       help="Regenerate .md files for all records in the database")
    group.add_argument("--gallery",          action="store_true",
                       help="Generate data/gallery.html for browsing downloaded images")

    parser.add_argument("--end",   metavar="YYYY-MM-DD",
                        help="End date for range (default: today, used with --start)")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if already exists")
    return parser.parse_args()


def main():
    args = parse_args()
    today = date.today()

    if args.rebuild_markdown:
        conn = init_db()
        rebuild_markdown_from_db(conn)
        conn.close()
        return

    if args.gallery:
        conn = init_db()
        build_gallery(conn)
        conn.close()
        return

    if args.today:
        dates = [today.isoformat()]

    elif args.date:
        dates = [args.date]

    elif args.latest:
        start = today - timedelta(days=args.latest - 1)
        dates = [d.isoformat() for d in date_range(start, today)]

    elif args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end   = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today
        if start > end:
            sys.exit("Error: --start must be before --end")
        # APOD launched June 16, 1995
        apod_launch = date(1995, 6, 16)
        if start < apod_launch:
            log.warning(f"APOD launched on 1995-06-16, adjusting start date.")
            start = apod_launch
        dates = [d.isoformat() for d in date_range(start, end)]

    print(f"NASA APOD Downloader  |  {len(dates)} date(s) to process")
    print(f"Storage: {IMAGES_DIR.resolve()}")
    print(f"Database: {DB_PATH.resolve()}\n")

    asyncio.run(run_batch(dates, skip_existing=not args.force))


if __name__ == "__main__":
    main()
