import http.server
import json
import os


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {"cwd": os.getcwd(), "mark": os.environ.get("BG_MARK"), "pid": os.getpid(), "ppid": os.getppid()}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


http.server.HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
