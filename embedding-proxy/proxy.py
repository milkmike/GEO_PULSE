#!/usr/bin/env python3
"""Lightweight embedding proxy server.

Runs on a machine without geo-restrictions and forwards embedding requests
to Jina AI / OpenAI. VPS connects via SSH tunnel or direct URL.

Usage:
    python proxy.py --port 9900
    # Then on VPS: ssh -R 9900:localhost:9900 user@macbook
    # Or set EMBEDDING_PROXY_URL=http://macbook-ip:9900/v1/embeddings
"""
import argparse
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("embedding-proxy")

PROXY_SECRET = ""


class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Auth check
        if PROXY_SECRET:
            secret = self.headers.get("X-Proxy-Secret", "")
            if secret != PROXY_SECRET:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error": "unauthorized"}')
                return

        try:
            data = json.loads(body)
            target_url = data.pop("_target_url", "https://api.jina.ai/v1/embeddings")
            target_auth = data.pop("_target_auth", "")

            req = urllib.request.Request(
                target_url,
                data=json.dumps(data).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": target_auth,
                    "User-Agent": "GeoPulse-EmbeddingProxy/1.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = resp.read()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(result)

            # Log
            result_data = json.loads(result)
            count = len(result_data.get("data", []))
            logger.info(f"Proxied {count} embeddings via {target_url.split('/')[2]}")

        except Exception as e:
            logger.error(f"Proxy error: {e}")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass  # Suppress default logging


def main():
    global PROXY_SECRET
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9900)
    parser.add_argument("--secret", type=str, default="")
    args = parser.parse_args()
    PROXY_SECRET = args.secret

    server = HTTPServer(("0.0.0.0", args.port), ProxyHandler)
    logger.info(f"Embedding proxy listening on :{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
