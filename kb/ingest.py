"""Import scripts, questionnaires and reference PDFs without sending them to AI."""
import argparse
import mimetypes
import sys
from pathlib import Path

import db
from kb.config import ROOT, data_dir
from kb.store import save_document


def import_file(conn, path, platform="EnjoySurvey", reference=False):
    path = Path(path).resolve()
    if path.stat().st_size > 20_000_000:
        raise ValueError("Maximum import size is 20 MB")
    mime = mimetypes.guess_type(path.name)[0] or "text/plain"
    return save_document(conn, url=path.as_uri(), title=path.name, platform=platform,
                         source_type="manual", raw=path.read_bytes(), mime=mime, reference=reference)


def bootstrap(conn):
    for path in ROOT.glob("ES 4.1*.pdf"):
        previous = conn.execute("""SELECT v.raw_path FROM kb_versions v JOIN kb_document_versions dv ON dv.version_id=v.id
            JOIN kb_documents d ON d.id=dv.document_id WHERE d.url='file:///references/enjoysurvey-4.1.pdf'
            ORDER BY dv.seen_at DESC,v.id DESC LIMIT 1""").fetchone()
        if previous:
            archived = data_dir() / previous["raw_path"]
            if archived.is_file() and archived.read_bytes() == path.read_bytes():
                continue
        save_document(conn, url="file:///references/enjoysurvey-4.1.pdf", title=path.name,
                      platform="EnjoySurvey", source_type="reference", raw=path.read_bytes(),
                      mime="application/pdf", reference=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--platform", default="EnjoySurvey")
    parser.add_argument("--reference", action="store_true")
    args = parser.parse_args(sys.argv[2:])
    with db.connect() as conn:
        db.init_schema(conn)
        version, created = import_file(conn, args.path, args.platform, args.reference)
        print(f"version={version}, new={created}")
