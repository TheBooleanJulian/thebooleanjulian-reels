"""Website mode: only reels-editor.thebooleanjulian.dev may pair with the studio and read its responses; the build
publishes the page and fonts and nothing else."""
import http.client, json, os, sys, threading
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import studio  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, 'web'))
import build  # noqa: E402

WEB = 'https://reels-editor.thebooleanjulian.dev'


@pytest.fixture(scope='module')
def port():
    srv = studio.Server(('127.0.0.1', 0), studio.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def call(port, path, method='GET', headers=None, body=None):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    c.request(method, path, body=body, headers={'Host': f'127.0.0.1:{port}', **(headers or {})})
    r = c.getresponse(); data = r.read(); c.close()
    return r.status, dict(r.getheaders()), data


def test_website_pairs_and_gets_the_token(port):
    code, hd, data = call(port, '/api/pair', headers={'Origin': WEB})
    j = json.loads(data)
    assert code == 200 and j == {'app': 'tbj-reels', 'token': studio.TOKEN, 'version': studio.API_VERSION}
    assert hd['Access-Control-Allow-Origin'] == WEB


@pytest.mark.parametrize('origin', [None, 'https://evil.example', 'https://reels-editor.thebooleanjulian.dev.evil.example',
                                    'http://reels-editor.thebooleanjulian.dev'])
def test_other_sites_cannot_pair(port, origin):
    code, hd, data = call(port, '/api/pair', headers={'Origin': origin} if origin else {})
    assert code == 403 and studio.TOKEN.encode() not in data
    assert 'Access-Control-Allow-Origin' not in hd


def test_preflight_only_for_the_website(port):
    code, hd, _ = call(port, '/api/save', 'OPTIONS', {'Origin': WEB, 'Access-Control-Request-Method': 'POST',
                                                      'Access-Control-Request-Private-Network': 'true'})
    assert code == 204 and hd['Access-Control-Allow-Private-Network'] == 'true'
    assert 'X-Token' in hd['Access-Control-Allow-Headers'] and hd['Access-Control-Allow-Origin'] == WEB
    code, hd, _ = call(port, '/api/save', 'OPTIONS', {'Origin': 'https://evil.example'})
    assert code == 403 and 'Access-Control-Allow-Origin' not in hd


def test_api_still_needs_the_token_from_the_website(port):
    assert call(port, '/api/env', headers={'Origin': WEB})[0] == 403
    code, hd, _ = call(port, '/api/env', headers={'Origin': WEB, 'X-Token': studio.TOKEN})
    assert code == 200 and hd['Access-Control-Allow-Origin'] == WEB


def test_host_check_still_blocks_rebinding(port):
    assert call(port, '/api/pair', headers={'Origin': WEB, 'Host': 'reels-editor.thebooleanjulian.dev'})[0] == 403


def test_build_publishes_page_and_fonts_only(tmp_path, monkeypatch):
    monkeypatch.setattr(build, 'DIST', str(tmp_path / 'dist'))
    fonts = build.build()
    files = sorted(os.path.relpath(os.path.join(d, f), build.DIST).replace(os.sep, '/')
                   for d, _, fs in os.walk(build.DIST) for f in fs)
    assert {'index.html', '_headers'} <= set(files)
    assert all(f in ('index.html', '_headers') or (f.startswith('fonts/') and f.endswith(('.ttf', '.txt'))) for f in files)
    assert {f'fonts/{n}' for n in fonts} <= set(files) and len(fonts) >= 7
    hdr = (tmp_path / 'dist' / '_headers').read_text()
    assert "connect-src 'self' http://127.0.0.1:*" in hdr and "frame-ancestors 'none'" in hdr


def test_dropped_connections_print_nothing(capsys):
    srv = studio.Server(('127.0.0.1', 0), studio.Handler)
    try:
        try: raise ConnectionResetError(10054, 'closed by the remote host')
        except ConnectionResetError: srv.handle_error(None, ('127.0.0.1', 1))
        assert capsys.readouterr().err == ''
    finally:
        srv.server_close()


def test_page_versions_match():
    page = open(build.PAGE, encoding='utf-8').read()
    assert f'const API_VERSION = {studio.API_VERSION};' in page
    assert page.count('__TOKEN__') == 1       # studio.py replaces every occurrence
