import re
import uuid
import sqlite3
from datetime import datetime
from pathlib import Path

import fitz
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse


UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

DB_FILE = "pdf_search.db"

app = FastAPI(title="PDF Madde Arama MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Madde başlığı yakalama
# Örnekler:
# MADDE 14
# MADDE 14 –
# MADDE 14.
# MADDE 14/A
# Madde 14
# MADDE NO: 14
ARTICLE_RE = re.compile(
    r"^\s*MADDE\s*(?:NO|No|no)?\s*[:\-–—.]?\s*(\d{1,4})(?:/([A-Za-zÇĞİÖŞÜçğıöşü]+))?",
    re.IGNORECASE
)


def tr_lower(text: str) -> str:
    """
    Basit Türkçe küçük harf dönüşümü.
    MVP için yeterlidir.
    """
    return text.replace("İ", "i").replace("I", "ı").lower()


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            original_name TEXT,
            stored_name TEXT,
            created_at TEXT,
            line_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'uploaded'
        );

        CREATE TABLE IF NOT EXISTS lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            page_no INTEGER NOT NULL,
            line_no_page INTEGER NOT NULL,
            line_no_global INTEGER NOT NULL,
            article_no TEXT,
            text TEXT NOT NULL,
            text_lower TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_lines_doc_text
        ON lines(doc_id, text_lower);

        CREATE INDEX IF NOT EXISTS idx_lines_doc_article
        ON lines(doc_id, article_no);
    """)

    conn.commit()
    conn.close()


init_db()


def parse_pdf(doc_id: str, pdf_path: Path) -> int:
    """
    PDF'yi satır satır okur, madde numarası ile birlikte SQLite'a yazar.
    """
    conn = get_db()
    cur = conn.cursor()

    # Daha önce aynı doc_id için satır varsa temizle
    cur.execute("DELETE FROM lines WHERE doc_id = ?", (doc_id,))

    pdf = fitz.open(str(pdf_path))

    current_article = None
    global_line = 0
    rows = []

    for page_no, page in enumerate(pdf, start=1):
        text = page.get_text("text")

        if not text or not text.strip():
            continue

        for line_no_page, raw_line in enumerate(text.splitlines(), start=1):
            line = " ".join(raw_line.split())

            if not line:
                continue

            match = ARTICLE_RE.match(line)

            if match:
                article_number = match.group(1)
                article_suffix = match.group(2)

                if article_suffix:
                    current_article = f"{article_number}/{article_suffix}"
                else:
                    current_article = article_number

            global_line += 1

            rows.append((
                doc_id,
                page_no,
                line_no_page,
                global_line,
                current_article,
                line,
                tr_lower(line)
            ))

    cur.executemany(
        """
        INSERT INTO lines (
            doc_id,
            page_no,
            line_no_page,
            line_no_global,
            article_no,
            text,
            text_lower
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows
    )

    conn.commit()
    conn.close()

    return global_line


@app.get("/", response_class=HTMLResponse)
def home():
    html_path = Path("index.html")

    if not html_path.exists():
        return """
        <h1>PDF Madde Arama API çalışıyor</h1>
        <p>index.html dosyası bulunamadı.</p>
        """

    return html_path.read_text(encoding="utf-8")


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)):
    """
    PDF yükler, metne çevirir ve satır satır indeksler.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Sadece PDF dosyası yükleyebilirsiniz."
        )

    content = await file.read()

    # 50 MB limiti
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="Dosya çok büyük. Maksimum 50 MB."
        )

    doc_id = str(uuid.uuid4())
    stored_name = f"{doc_id}.pdf"
    pdf_path = UPLOAD_DIR / stored_name

    pdf_path.write_bytes(content)

    # PDF açılıyor mu ve metin var mı kontrol et
    try:
        pdf = fitz.open(str(pdf_path))
        sample_text = ""

        for i, page in enumerate(pdf):
            if i >= 3:
                break
            sample_text += page.get_text("text")

        pdf.close()

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"PDF okunamadı: {str(e)}"
        )

    is_probably_scanned = len(sample_text.strip()) < 20

    conn = get_db()
    conn.execute(
        """
        INSERT INTO documents (
            id,
            original_name,
            stored_name,
            created_at,
            line_count,
            status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            file.filename,
            stored_name,
            datetime.utcnow().isoformat(),
            0,
            "processing"
        )
    )
    conn.commit()
    conn.close()

    try:
        line_count = parse_pdf(doc_id, pdf_path)
    except Exception as e:
        conn = get_db()
        conn.execute(
            "UPDATE documents SET status = 'failed' WHERE id = ?",
            (doc_id,)
        )
        conn.commit()
        conn.close()

        raise HTTPException(
            status_code=500,
            detail=f"PDF işlenemedi: {str(e)}"
        )

    status = "ready" if line_count > 0 else "empty"

    conn = get_db()
    conn.execute(
        """
        UPDATE documents
        SET line_count = ?, status = ?
        WHERE id = ?
        """,
        (line_count, status, doc_id)
    )
    conn.commit()
    conn.close()

    response = {
        "id": doc_id,
        "filename": file.filename,
        "line_count": line_count,
        "status": status
    }

    if is_probably_scanned:
        response["warning"] = (
            "PDF taranmış veya metin katmanı çok az olabilir. OCR gerekebilir."
        )

    return response


@app.get("/api/documents")
def list_documents():
    """
    Yüklenen PDF'leri listeler.
    """
    conn = get_db()

    rows = conn.execute(
        """
        SELECT id, original_name, created_at, line_count, status
        FROM documents
        ORDER BY created_at DESC
        """
    ).fetchall()

    conn.close()

    return rows


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    """
    Tek bir PDF'nin durumunu getirir.
    """
    conn = get_db()

    row = conn.execute(
        """
        SELECT id, original_name, created_at, line_count, status
        FROM documents
        WHERE id = ?
        """,
        (doc_id,)
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="PDF bulunamadı.")

    return row


@app.get("/api/documents/{doc_id}/search")
def search_document(doc_id: str, q: str, limit: int = 200):
    """
    Yüklenen PDF içinde kelime arar.
    """
    q = q.strip()

    if len(q) < 2:
        raise HTTPException(
            status_code=400,
            detail="Arama kelimesi en az 2 karakter olmalı."
        )

    if limit > 1000:
        limit = 1000

    q_lower = tr_lower(q)

    conn = get_db()

    rows = conn.execute(
        """
        SELECT
            page_no,
            line_no_page,
            line_no_global,
            article_no,
            text
        FROM lines
        WHERE doc_id = ?
          AND text_lower LIKE ?
        ORDER BY page_no, line_no_page
        LIMIT ?
        """,
        (doc_id, f"%{q_lower}%", limit)
    ).fetchall()

    conn.close()

    results = []

    for row in rows:
        results.append({
            "page": row["page_no"],
            "line_on_page": row["line_no_page"],
            "global_line": row["line_no_global"],
            "article_no": row["article_no"],
            "text": row["text"]
        })

    return {
        "doc_id": doc_id,
        "query": q,
        "count": len(results),
        "results": results
    }
