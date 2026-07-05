from flask import Flask, request, jsonify, redirect, abort
from models import init_db, SessionLocal, URL
from utils import encode_base62
from urllib.parse import urlparse

app = Flask(__name__)
init_db()

def is_valid_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ('http', 'https') and bool(p.netloc)
    except Exception:
        return False

@app.route('/shorten', methods=['POST'])
def shorten():
    data = request.get_json() or {}
    long_url = data.get('url') or data.get('long_url')
    if not long_url or not is_valid_url(long_url):
        return jsonify({'error': 'invalid url'}), 400

    session = SessionLocal()
    try:
        # Insert a row to obtain an auto-increment id
        new_url = URL(long_url=long_url)
        session.add(new_url)
        session.commit()
        session.refresh(new_url)

        # Generate short code from numeric id and save
        code = encode_base62(new_url.id)
        new_url.short_code = code
        session.commit()

        short_url = request.host_url.rstrip('/') + '/' + code
        return jsonify({'short_url': short_url, 'code': code, 'id': new_url.id}), 201
    finally:
        session.close()

@app.route('/<code>')
def redirect_short(code):
    session = SessionLocal()
    try:
        obj = session.query(URL).filter_by(short_code=code).one_or_none()
        if not obj:
            abort(404)
        return redirect(obj.long_url, code=302)
    finally:
        session.close()

@app.route('/api/urls', methods=['GET'])
def list_urls():
    session = SessionLocal()
    try:
        rows = session.query(URL).order_by(URL.created_at.desc()).all()
        return jsonify([{
            'id': r.id,
            'code': r.short_code,
            'url': r.long_url,
            'created_at': r.created_at.isoformat() if r.created_at else None
        } for r in rows])
    finally:
        session.close()

@app.route('/api/urls/<code>', methods=['DELETE'])
def delete_url(code):
    session = SessionLocal()
    try:
        obj = session.query(URL).filter_by(short_code=code).one_or_none()
        if not obj:
            return jsonify({'error': 'not found'}), 404
        session.delete(obj)
        session.commit()
        return jsonify({'deleted': code})
    finally:
        session.close()

if __name__ == '__main__':
    app.run(debug=True)