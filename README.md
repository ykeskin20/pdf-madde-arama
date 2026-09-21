# PDF Madde Arama MVP

Bu proje, PDF yükleyip PDF içinde kelime araması yapmanızı sağlar.

Arama sonuçlarında kelimenin geçtiği:

- sayfa
- satır
- madde
- ilgili satır metni

gösterilir.

## Özellikler

- PDF yükleme
- PDF metin çıkarma
- Satır bazlı indeksleme
- Basit madde tespiti
- Kelime arama
- Sayfa, satır ve madde bilgisiyle sonuç listeleme

## Teknolojiler

- FastAPI
- PyMuPDF
- SQLite
- Vanilla JavaScript

## Kurulum

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt# pdf-madde-arama
