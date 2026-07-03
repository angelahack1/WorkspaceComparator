"""
WorkSpaceComparator standalone launcher.

This is the entry point PyInstaller uses to build WorkSpaceComparator.exe.
It boots the embedded Django application on http://127.0.0.1:9000, waits
until the server is fully loaded and accepting connections, and 5 seconds
after that opens the user's default browser on the app.

Optional overrides (mainly for testing/automation):
    --no-browser        do not open the browser (same as WSC_NO_BROWSER=1)
    WSC_PORT=<number>   serve on a different port (default: 9000)
"""
import os
import socket
import sys
import threading
import time
import webbrowser

HOST = '127.0.0.1'
DEFAULT_PORT = 9000
BROWSER_DELAY_SECONDS = 5      # extra wait AFTER the server is confirmed up
READY_TIMEOUT_SECONDS = 60     # give up waiting for the server after this


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def _browser_opener(port: int) -> None:
    """Wait until the server answers on the port, then open the browser."""
    deadline = time.time() + READY_TIMEOUT_SECONDS
    while time.time() < deadline:
        if _port_in_use(port):
            break
        time.sleep(0.25)
    else:
        print('WARNING: server did not come up in time; browser not opened.')
        return
    print(f'Server is up. Opening browser in {BROWSER_DELAY_SECONDS} seconds...')
    time.sleep(BROWSER_DELAY_SECONDS)
    url = f'http://{HOST}:{port}/'
    print(f'Opening default browser at {url}')
    webbrowser.open(url)


def main() -> int:
    port = int(os.environ.get('WSC_PORT', DEFAULT_PORT))
    no_browser = ('--no-browser' in sys.argv[1:]
                  or os.environ.get('WSC_NO_BROWSER') == '1')

    print('=' * 60)
    print('  Workspace Comparator - standalone server')
    print('=' * 60)

    if _port_in_use(port):
        print(f'ERROR: port {port} is already in use on {HOST}.')
        print('Close the other application (or another running copy of this')
        print('tool) and try again, or set the WSC_PORT environment variable.')
        try:
            input('Press Enter to exit...')
        except EOFError:
            pass
        return 1

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'workspace_comparator.settings')

    import django
    django.setup()

    from django.core.servers.basehttp import ThreadedWSGIServer, WSGIRequestHandler
    from django.core.wsgi import get_wsgi_application

    application = get_wsgi_application()

    httpd = ThreadedWSGIServer((HOST, port), WSGIRequestHandler)
    httpd.set_app(application)

    if not no_browser:
        threading.Thread(target=_browser_opener, args=(port,), daemon=True).start()

    print(f'Starting server at http://{HOST}:{port}/')
    print('Keep this window open while using the application.')
    print('Press Ctrl+C (or close this window) to stop the server.')
    print('-' * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nServer stopped. Goodbye!')
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
