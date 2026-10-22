"""Phase-one Spotify source reader. No credentials, API requests or music writes."""
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

DEFAULT_PLAYLIST = '37i9dQZEVXcT3aus8zyY7o'
_ID = re.compile(r'^[A-Za-z0-9]{22}$')
_LOCK = threading.Lock()


def playlist_id(value):
    if _ID.fullmatch(value):
        return value
    url = urlsplit(value)
    match = re.fullmatch(r'/playlist/([A-Za-z0-9]{22})/?', url.path)
    if url.scheme == 'https' and url.netloc == 'open.spotify.com' and match and not url.fragment:
        return match[1]
    raise ValueError('Supply a Spotify playlist ID or https://open.spotify.com/playlist/ID')


def validate(raw, target):
    """Normalize only allowlisted metadata outside the browser owner thread."""
    if raw.get('auth'):
        return 'source_auth_required', None
    if not raw.get('at_end') or raw.get('error') or raw.get('loading') or raw.get('identity') != target or raw.get('name') != 'Discover Weekly':
        return 'source_incomplete', None
    rows = raw.get('tracks', [])
    if len(rows) != 30:
        return 'source_incomplete', None
    result = []
    for position, row in enumerate(rows, 1):
        artists = row.get('artists')
        if (type(row.get('position')) is not int or row['position'] != position
                or not isinstance(row.get('id'), str) or not _ID.fullmatch(row['id'])
                or not isinstance(row.get('title'), str) or not row['title'].strip()
                or not isinstance(artists, list) or not artists
                or any(not isinstance(a, str) or not a.strip() for a in artists)):
            return 'source_incomplete', None
        item = dict(position=position, id=row['id'], title=row['title'].strip(),
                    artists=[a.strip() for a in artists])
        if isinstance(row.get('album'), str) and row['album'].strip():
            item['album'] = row['album'].strip()
        duration = row.get('duration')
        if isinstance(duration, str) and re.fullmatch(r'\d{1,3}:[0-5]\d', duration):
            minutes, seconds = map(int, duration.split(':'))
            item['duration_ms'] = (minutes * 60 + seconds) * 1000
        result.append(item)
    return 'ok', result


def finish(first, second, target):
    status, tracks = validate(first, target)
    if status != 'ok':
        return {'status': status}
    status, again = validate(second, target)
    if status != 'ok':
        return {'status': status}
    if tracks != again:
        return {'status': 'source_unstable'}
    source = {'service': 'spotify', 'playlist_id': target,
              'url': f'https://open.spotify.com/playlist/{target}', 'name': 'Discover Weekly'}
    canonical = json.dumps({'source': source, 'tracks': tracks}, sort_keys=True,
                           separators=(',', ':'), ensure_ascii=False).encode()
    return dict(status='ok', source=source, tracks=tracks,
                extracted_at=datetime.now(timezone.utc).isoformat(),
                fingerprint=hashlib.sha256(canonical).hexdigest(), fingerprint_version=1)


# Use semantic roles and link destinations, never generated CSS classes. Only
# return track metadata/state; never serialize page HTML, storage or responses.
_READ = r"""() => {
 const main = document.querySelector('main');
 const text = main?.innerText || '';
 const path = location.pathname.match(/^\/playlist\/([A-Za-z0-9]{22})\/?$/);
 const name = main?.querySelector('h1')?.textContent.trim() || '';
 const tracks = [...(main?.querySelectorAll('[role="row"]') || [])].flatMap(row => {
   const link = row.querySelector('a[href*="/track/"]');
   if (!link) return [];
   const cells = [...row.querySelectorAll('[role="gridcell"]')];
   const number = cells[0]?.textContent.trim();
   const position = /^\d+$/.test(number || '') ? Number(number) : Number(row.getAttribute('aria-rowindex')) - 1;
   const artists = [...row.querySelectorAll('a[href*="/artist/"]')].map(a => a.textContent.trim());
   return [{position, id: (link.getAttribute('href').match(/\/track\/([A-Za-z0-9]{22})(?:[?/#]|$)/) || [])[1] || '',
     title: link.textContent.trim(), artists,
     album: row.querySelector('a[href*="/album/"]')?.textContent.trim() || '',
     duration: cells.at(-1)?.textContent.trim() || ''}];
 });
 let scroller = main?.querySelector('[role="row"]')?.parentElement;
 while (scroller && !(scroller.scrollHeight > scroller.clientHeight + 10 && /auto|scroll/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
 const at_end = !scroller || scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2;
 return {identity: path?.[1] || '', name, tracks, at_end,
   auth: location.hostname === 'accounts.spotify.com' || /log in to (view|see|access)|sign in to (view|see|access)/i.test(text),
   error: /something went wrong|couldn.t find that playlist|page not found|playlist is unavailable/i.test(text),
   loading: !!main?.querySelector('[aria-busy="true"], [role="progressbar"]')};
}"""
_SCROLL = r"""reset => {
 const row = document.querySelector('main [role="row"]');
 let node = row?.parentElement;
 while (node && !(node.scrollHeight > node.clientHeight + 10 && /auto|scroll/.test(getComputedStyle(node).overflowY))) node = node.parentElement;
 if (!node) return false;
 node.scrollTop = reset ? 0 : node.scrollTop + Math.max(100, node.clientHeight * 0.7);
 return true;
}"""


def read_spotify_discover_weekly(playlist: str = DEFAULT_PLAYLIST) -> dict:
    """Read and validate 30 Discover Weekly positions twice. No Apple Music writes.

    playlist is an explicit Spotify playlist ID/URL; the known ID is the default.
    Login, incomplete and unstable sources fail closed. Temporary tab always closes.
    """
    from applemusic_mcp import browser
    target = playlist_id(playlist)
    if not _LOCK.acquire(blocking=False):
        return {'status': 'source_busy'}
    tab = []  # Playwright objects are accessed only inside engine callbacks.
    def open_tab(primary):
        page = primary.context.new_page()
        tab.append(page)
        page.goto(f'https://open.spotify.com/playlist/{target}', wait_until='commit', timeout=5000)
    def collect():
        browser._engine.submit(lambda _: tab[0].evaluate(_SCROLL, True))
        rows = {}
        state = {}
        for _ in range(24):
            time.sleep(0.35)  # Never sleep on the Playwright owner thread.
            state = browser._engine.submit(lambda _: tab[0].evaluate(_READ))
            for row in state['tracks']:
                position = row['position']
                if position in rows and rows[position] != row:
                    return dict(state, error=True)
                rows[position] = row
            if state['auth'] or state['error'] or len(rows) > 30:
                break
            if len(rows) >= 30 and state['at_end'] and not state['loading']:
                break
            browser._engine.submit(lambda _: tab[0].evaluate(_SCROLL, False))
        return dict(state, tracks=[rows[k] for k in sorted(rows)])
    try:
        browser._engine.submit(open_tab)
        first = collect()
        status, _ = validate(first, target)
        if status != 'ok':
            result = {'status': status}
        else:
            result = finish(first, collect(), target)
    except Exception:
        # Browser exceptions can contain URLs or page content: never expose them.
        result = {'status': 'source_incomplete'}
    finally:
        try:
            if tab:
                browser._engine.submit(lambda _: tab[0].close())
        except Exception:
            result = {'status': 'source_incomplete'}
        finally:
            _LOCK.release()
    return result
