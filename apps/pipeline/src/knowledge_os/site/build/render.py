"""Render dependency-free PWA support assets."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any, Mapping

def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _make_icon(size: int) -> bytes:
    """Generate a small geometric RGBA PNG without an image dependency."""

    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            nx = x / max(1, size - 1)
            ny = y / max(1, size - 1)
            red = int(20 + 24 * nx)
            green = int(31 + 31 * ny)
            blue = int(49 + 25 * (1 - nx))
            # Three warm parent/child nodes joined by a subtle diagonal.
            radius = size * 0.105
            centers = (
                (size * 0.31, size * 0.29),
                (size * 0.52, size * 0.51),
                (size * 0.72, size * 0.72),
            )
            on_node = any(
                (x - cx) ** 2 + (y - cy) ** 2 <= radius**2
                for cx, cy in centers
            )
            on_link = abs((y - x) - size * 0.01) < size * 0.027
            if on_node:
                red, green, blue = 240, 180, 93
            elif on_link and size * 0.25 < x < size * 0.78:
                red, green, blue = 92, 161, 154
            row.extend((red, green, blue, 255))
        rows.append(bytes(row))
    raw = b"".join(rows)
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        signature
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _render_index(template: str, data: Mapping[str, Any], noindex: bool) -> str:
    site = data["site"]
    replacements = {
        "{{SITE_TITLE}}": _html_escape(site["title"]),
        "{{SITE_DESCRIPTION}}": _html_escape(site["description"]),
        "{{LANGUAGE}}": _html_escape(site["language"]),
        "{{ROBOTS_META}}": (
            "noindex, nofollow, noarchive, nosnippet"
            if noindex
            else "index, follow"
        ),
    }
    for marker, replacement in replacements.items():
        template = template.replace(marker, replacement)
    return template


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _headers(private: bool, noindex: bool) -> str:
    robot_header = (
        "\n  X-Robots-Tag: noindex, nofollow, noarchive, nosnippet"
        if noindex
        else ""
    )
    data_cache = (
        "private, no-store, max-age=0"
        if private
        else "public, max-age=300, must-revalidate"
    )
    return f"""/*
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; worker-src 'self'; manifest-src 'self'; upgrade-insecure-requests
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()
  Cross-Origin-Opener-Policy: same-origin
  Cross-Origin-Resource-Policy: same-origin
  Cache-Control: public, max-age=0, must-revalidate{robot_header}

/data/*
  Cache-Control: {data_cache}

/build-meta.json
  Cache-Control: private, no-store, max-age=0

/__knowledge/*
  Cache-Control: private, no-store, max-age=0

/assets/*
  Cache-Control: public, max-age=300, must-revalidate

/icons/*
  Cache-Control: public, max-age=31536000, immutable
"""


def _service_worker(cache_version: str, private: bool) -> str:
    shell_files = [
        "./",
        "./index.html",
        "./assets/styles.css",
        "./assets/markdown-reader.js",
        "./assets/markdown-reader.css",
        "./assets/reading-history.js",
        "./assets/reading-actions.js",
        "./assets/data-source.js",
        "./assets/search-utils.js",
        "./assets/related-documents.js",
        "./assets/app.js",
        "./assets/local-ingest.css",
        "./assets/local-ingest.js",
        "./assets/local-classification.css",
        "./assets/local-classification.js",
        "./assets/local-review.css",
        "./assets/local-review.js",
        "./assets/local-review-queue.css",
        "./assets/local-review-queue.js",
        "./manifest.webmanifest",
        "./offline.html",
        "./icons/icon-192.png",
        "./icons/icon-512.png",
    ]
    public_data = [
        "./data/site-data.json",
        "./data/taxonomy.json",
        "./data/search-index.json",
        "./data/graph.json",
    ]
    cache_files = shell_files if private else shell_files + public_data
    cache_literal = json.dumps(cache_files, ensure_ascii=False)
    cache_name = (
        "knowledge-os-private-shell-v3-{}".format(cache_version)
        if private
        else "knowledge-os-{}".format(cache_version)
    )
    data_policy = """
  if (pathname.includes("/data/")) {
    event.respondWith(fetch(event.request, { cache: "no-store" }));
    return;
  }
""" if private else ""
    runtime_strategy = """
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.open(CACHE_NAME).then((cache) =>
        cache.match(event.request).then((cached) =>
          cached || cache.match("./offline.html")
        )
      )
    )
  );
""" if private else """
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.open(CACHE_NAME).then((cache) =>
        cache.match(event.request).then((cached) => cached || cache.match("./offline.html"))
      ))
  );
"""
    return f"""const CACHE_NAME = "{cache_name}";
const CACHE_FILES = {cache_literal};

function decodedPathname(url) {{
  try {{
    return decodeURIComponent(url.pathname);
  }} catch (_error) {{
    return url.pathname;
  }}
}}

self.addEventListener("install", (event) => {{
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(CACHE_FILES)));
  self.skipWaiting();
}});

self.addEventListener("activate", (event) => {{
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
}});

self.addEventListener("fetch", (event) => {{
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  const pathname = decodedPathname(url).toLocaleLowerCase();
  if (pathname.includes("/__knowledge/") || pathname.endsWith("/build-meta.json")) {{
    event.respondWith(fetch(event.request, {{ cache: "no-store" }}));
    return;
  }}
{data_policy}
{runtime_strategy}
}});
"""


def _manifest(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": data["site"]["title"],
        "short_name": "知识体系",
        "description": data["site"]["description"],
        "lang": data["site"]["language"],
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#f4f2eb",
        "theme_color": "#141f31",
        "icons": [
            {
                "src": "./icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "./icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }
