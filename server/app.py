import hashlib
import io
import json
import os
import sqlite3
import time

from flask import Flask, Response, g, jsonify, request, send_from_directory
from PIL import Image, ImageDraw, ImageFont

DB_PATH = os.environ.get("DB_PATH", "/data/todo.db")
API_TOKEN = os.environ.get("API_TOKEN", "")
WIDTH, HEIGHT = 1072, 1448
DONE_TTL = 24 * 3600
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

app = Flask(__name__, static_folder="static")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            position REAL NOT NULL,
            done_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    conn.commit()
    conn.close()


def get_state(key, default=None):
    row = db().execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key, value):
    db().execute(
        "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    db().commit()


def items():
    now = int(time.time())
    db().execute("DELETE FROM items WHERE done_at IS NOT NULL AND done_at < ?", (now - DONE_TTL,))
    db().commit()
    rows = db().execute(
        "SELECT id, text, done_at FROM items ORDER BY done_at IS NOT NULL, position"
    ).fetchall()
    return [{"id": r["id"], "text": r["text"], "done": r["done_at"] is not None} for r in rows]


def check_token():
    if not API_TOKEN:
        return True
    return request.headers.get("Authorization") == f"Bearer {API_TOKEN}" or request.args.get("token") == API_TOKEN


# ---------- web UI / API ----------

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/items")
def list_items():
    return jsonify(items())


@app.post("/api/items")
def add_item():
    text = (request.get_json(silent=True) or {}).get("text") or request.form.get("text") or request.args.get("text")
    text = (text or "").strip()
    if not text:
        return jsonify(error="text required"), 400
    pos = db().execute("SELECT COALESCE(MAX(position), 0) + 1 FROM items").fetchone()[0]
    db().execute("INSERT INTO items(text, position) VALUES(?, ?)", (text[:200], pos))
    db().commit()
    return jsonify(items())


@app.post("/api/add")
def add_token():
    """For phone shortcuts / Home Assistant: requires API_TOKEN."""
    if not check_token():
        return jsonify(error="unauthorized"), 401
    return add_item()


@app.patch("/api/items/<int:item_id>")
def update_item(item_id):
    body = request.get_json(silent=True) or {}
    if "done" in body:
        db().execute("UPDATE items SET done_at=? WHERE id=?", (int(time.time()) if body["done"] else None, item_id))
    if "text" in body and body["text"].strip():
        db().execute("UPDATE items SET text=? WHERE id=?", (body["text"].strip()[:200], item_id))
    db().commit()
    return jsonify(items())


@app.delete("/api/items/<int:item_id>")
def delete_item(item_id):
    db().execute("DELETE FROM items WHERE id=?", (item_id,))
    db().commit()
    return jsonify(items())


@app.post("/api/order")
def reorder():
    ids = (request.get_json(silent=True) or {}).get("ids", [])
    for i, item_id in enumerate(ids):
        db().execute("UPDATE items SET position=? WHERE id=?", (i, int(item_id)))
    db().commit()
    return jsonify(items())


@app.post("/api/clear-done")
def clear_done():
    db().execute("DELETE FROM items WHERE done_at IS NOT NULL")
    db().commit()
    return jsonify(items())


MAX_ROWS, MAX_COLS, MAX_CELL = 12, 8, 60


def get_table():
    try:
        return json.loads(get_state("table", "[]"))
    except ValueError:
        return []


@app.get("/api/table")
def read_table():
    return jsonify(rows=get_table())


@app.put("/api/table")
def write_table():
    rows = (request.get_json(silent=True) or {}).get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
        return jsonify(error="rows must be a list of lists"), 400
    clean = []
    for r in rows[:MAX_ROWS]:
        cells = [str(c).strip()[:MAX_CELL] for c in r[:MAX_COLS]]
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            clean.append(cells)
    set_state("table", json.dumps(clean))
    return jsonify(rows=clean)


@app.get("/api/kindle")
def kindle_status():
    return jsonify(
        last_seen=int(get_state("last_seen", 0)),
        battery=get_state("battery"),
        stay_awake_until=int(get_state("stay_awake_until", 0)),
        now=int(time.time()),
    )


@app.post("/api/kindle/stay-awake")
def stay_awake():
    minutes = int((request.get_json(silent=True) or {}).get("minutes", 0))
    set_state("stay_awake_until", int(time.time()) + minutes * 60 if minutes > 0 else 0)
    return kindle_status()


# ---------- Kindle image ----------

def wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def table_layout(d, table, margin=70):
    """Lay out every row on its own: a row's cells share the row's full width.

    Cell widths in a row are proportional to their content. Use the largest font
    where no row needs wrapping; if even the smallest font overflows, the cells
    of that row get their proportional share and their text wraps.
    """
    avail, pad = WIDTH - 2 * margin, 14

    def natural(r, font, bold):
        return [int(d.textlength(c, font=bold if i == 0 else font)) + 2 * pad for i, c in enumerate(r)]

    for size in (40, 36, 32, 28, 24):
        font = ImageFont.truetype(FONT_REG, size)
        bold = ImageFont.truetype(FONT_BOLD, size)
        if all(sum(natural(r, font, bold)) <= avail for r in table):
            break

    line_h = int(size * 1.25)
    rows = []
    for r in table:
        nat = natural(r, font, bold)
        widths = [avail * w // sum(nat) for w in nat]
        widths[-1] += avail - sum(widths)  # absorb rounding so the row spans the full width
        cells = [wrap(d, c, bold if i == 0 else font, widths[i] - 2 * pad) for i, c in enumerate(r)]
        rows.append((cells, widths, max(len(c) for c in cells) * line_h + 2 * pad))
    return dict(font=font, bold=bold, rows=rows, line_h=line_h, pad=pad,
                margin=margin, h=sum(h for _, _, h in rows))


def draw_table(d, tl, top):
    y = top
    for cells, widths, h in tl["rows"]:
        x = tl["margin"]
        for i, lines in enumerate(cells):
            w = widths[i]
            d.rectangle((x, y, x + w, y + h), outline=140, width=2)
            for j, line in enumerate(lines):
                d.text((x + tl["pad"], y + tl["pad"] + j * tl["line_h"]), line,
                       font=tl["bold"] if i == 0 else tl["font"], fill=0)
            x += w
        y += h


def render(todo, table=()):
    img = Image.new("L", (WIDTH, HEIGHT), 255)
    d = ImageDraw.Draw(img)
    margin, bottom = 70, HEIGHT - 80

    if table:
        tl = table_layout(d, table, margin)
        bottom -= tl["h"] + 40
        draw_table(d, tl, HEIGHT - 80 - tl["h"])
        d.line((margin, bottom + 10, WIDTH - margin, bottom + 10), fill=0, width=4)

    title = ImageFont.truetype(FONT_BOLD, 84)
    d.text((margin, 60), "To do", font=title, fill=0)
    d.line((margin, 175, WIDTH - margin, 175), fill=0, width=4)

    open_items = [t for t in todo if not t["done"]]
    done_items = [t for t in todo if t["done"]]
    if not todo:
        d.text((margin, 240), "All done!", font=ImageFont.truetype(FONT_REG, 60), fill=90)
        return img

    # Pick the largest font size that fits all items.
    for size in (56, 50, 44, 40, 36, 32, 28):
        font = ImageFont.truetype(FONT_REG, size)
        line_h, gap, box = int(size * 1.25), int(size * 0.55), int(size * 0.8)
        text_x = margin + box + 30
        blocks = [(t, wrap(d, t["text"], font, WIDTH - margin - text_x)) for t in open_items + done_items]
        total = sum(len(l) * line_h + gap for _, l in blocks)
        if total <= bottom - 210:
            break

    y, hidden = 210, 0
    for idx, (t, lines) in enumerate(blocks):
        h = len(lines) * line_h
        if y + h > bottom - line_h:
            hidden = len(blocks) - idx
            break
        by = y + (line_h - box) // 2 + 4
        color = 140 if t["done"] else 0
        d.rectangle((margin, by, margin + box, by + box), outline=color, width=4)
        if t["done"]:
            d.line((margin + 6, by + box // 2, margin + box // 2.5, by + box - 8), fill=color, width=6)
            d.line((margin + box // 2.5, by + box - 8, margin + box - 4, by + 6), fill=color, width=6)
        for i, line in enumerate(lines):
            ly = y + i * line_h
            d.text((text_x, ly), line, font=font, fill=color)
            if t["done"]:
                mid = ly + line_h // 2 + 2
                d.line((text_x, mid, text_x + d.textlength(line, font=font), mid), fill=color, width=3)
        y += h + gap
    if hidden:
        d.text((text_x, y), f"+ {hidden} more…", font=font, fill=90)
    return img


@app.get("/kindle.png")
def kindle_png():
    bat = request.args.get("bat")
    set_state("last_seen", int(time.time()))
    if bat:
        set_state("battery", bat)

    todo, table = items(), get_table()
    etag = hashlib.sha1(repr((todo, table)).encode()).hexdigest()[:16]
    headers = {"ETag": f'"{etag}"', "Cache-Control": "no-cache"}
    until = int(get_state("stay_awake_until", 0))
    if until > time.time():
        headers["X-Stay-Awake"] = str(until - int(time.time()))

    if request.headers.get("If-None-Match") == f'"{etag}"':
        return Response(status=304, headers=headers)

    buf = io.BytesIO()
    render(todo, table).save(buf, "PNG", optimize=True)
    return Response(buf.getvalue(), mimetype="image/png", headers=headers)


init_db()
