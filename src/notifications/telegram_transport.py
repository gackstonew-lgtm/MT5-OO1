"""
Low-level Network Transport for Telegram Bot API.
Supports Direct HTTPS and SOCKS5 / HTTP Proxy tunneling with automatic retries,
exponential backoff, SSL handling, and HTTP 429 (Too Many Requests) rate limit honoring.
"""

from __future__ import annotations
import socket
import ssl
import struct
import json
import time
import logging
import html
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("TelegramTransport")


def escape_html(text: Any) -> str:
    """Escapes raw dynamic text to prevent breaking Telegram HTML markup."""
    if text is None:
        return ""
    return html.escape(str(text))


def truncate_message(text: str, max_chars: int = 4096) -> str:
    """Clamps message to Telegram maximum permissible length (4096 characters)."""
    if len(text) <= max_chars:
        return text
    trunc_tag = "\n\n<i>[Message truncated...]</i>"
    return text[: max_chars - len(trunc_tag)] + trunc_tag


class TelegramTransport:
    def __init__(self, bot_token: str, proxy: Optional[str] = None):
        self.bot_token = bot_token
        self.proxy = proxy
        self.api_host = "api.telegram.org"
        self.api_port = 443

    def _parse_proxy(self) -> Optional[Tuple[str, str, int]]:
        """Parses proxy URI into (scheme, host, port)."""
        if not self.proxy:
            return None
        parsed = urlparse(self.proxy)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (1080 if "socks" in scheme else 8080)
        return (scheme, host, port)

    def _connect_socks5(self, proxy_host: str, proxy_port: int, dest_host: str, dest_port: int, timeout: float) -> socket.socket:
        """Establishes SOCKS5 handshake and creates TCP tunnel."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((proxy_host, proxy_port))

        # Greeting: SOCKS version 5, 1 auth method (0x00 = No Auth)
        sock.sendall(b"\x05\x01\x00")
        auth_resp = sock.recv(2)
        if len(auth_resp) < 2 or auth_resp[0] != 0x05 or auth_resp[1] != 0x00:
            sock.close()
            raise ConnectionError(f"SOCKS5 proxy rejected authentication on {proxy_host}:{proxy_port}")

        # CONNECT request: CMD 0x01, RSV 0x00, ATYP 0x03 (Domain name)
        host_bytes = dest_host.encode("utf-8")
        req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack(">H", dest_port)
        sock.sendall(req)

        resp = sock.recv(10)
        if len(resp) < 2 or resp[1] != 0x00:
            err_code = resp[1] if len(resp) > 1 else -1
            sock.close()
            raise ConnectionError(f"SOCKS5 proxy connection to {dest_host}:{dest_port} failed with code {err_code}")

        return sock

    def _open_ssl_socket(self, timeout: float = 10.0) -> socket.socket:
        """Opens raw TLS socket connected to api.telegram.org (direct or via proxy)."""
        proxy_info = self._parse_proxy()

        if proxy_info and "socks" in proxy_info[0]:
            scheme, p_host, p_port = proxy_info
            raw_sock = self._connect_socks5(p_host, p_port, self.api_host, self.api_port, timeout)
        else:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(timeout)
            raw_sock.connect((self.api_host, self.api_port))

        # SSL Wrap
        ctx = ssl._create_unverified_context() if proxy_info else ssl.create_default_context()
        try:
            ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=self.api_host)
        except ssl.SSLError:
            # Reopen socket if wrap failed
            raw_sock.close()
            if proxy_info and "socks" in proxy_info[0]:
                scheme, p_host, p_port = proxy_info
                raw_sock = self._connect_socks5(p_host, p_port, self.api_host, self.api_port, timeout)
            else:
                raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw_sock.settimeout(timeout)
                raw_sock.connect((self.api_host, self.api_port))
            ctx = ssl._create_unverified_context()
            ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=self.api_host)

        ssl_sock.settimeout(timeout)
        return ssl_sock

    def request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
        max_retries: int = 3
    ) -> Tuple[int, Dict[str, Any], str]:
        """
        Executes HTTP request against Telegram Bot API with retries, backoff, and 429 rate limit honoring.
        Returns (http_status, parsed_json_dict, raw_response_body).
        """
        path = f"/bot{self.bot_token}/{endpoint}"
        body_bytes = b""
        headers = [
            f"Host: {self.api_host}",
            "Connection: close",
            "User-Agent: MT5-Institutional-Bot/3.0"
        ]

        if payload is not None:
            body_bytes = json.dumps(payload).encode("utf-8")
            headers.append("Content-Type: application/json")
            headers.append(f"Content-Length: {len(body_bytes)}")

        raw_req = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n"
        req_bytes = raw_req.encode("utf-8") + body_bytes

        attempt = 0
        backoff = 1.0

        while attempt < max_retries:
            attempt += 1
            sock = None
            try:
                sock = self._open_ssl_socket(timeout=timeout)
                sock.sendall(req_bytes)

                raw_resp = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    raw_resp += chunk

                if not raw_resp:
                    raise ConnectionResetError("Empty response from Telegram API")

                # Parse HTTP response
                header_part, _, body_part = raw_resp.partition(b"\r\n\r\n")
                status_line = header_part.split(b"\r\n")[0].decode("utf-8", errors="ignore")
                parts = status_line.split(" ", 2)
                status_code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 500

                body_text = body_part.decode("utf-8", errors="ignore")
                parsed_json = {}
                try:
                    parsed_json = json.loads(body_text)
                except Exception:
                    pass

                # Check for HTTP 429 (Too Many Requests)
                if status_code == 429:
                    retry_after = 5
                    if "parameters" in parsed_json and "retry_after" in parsed_json["parameters"]:
                        retry_after = int(parsed_json["parameters"]["retry_after"])
                    logger.warning(f"[Telegram] Rate limit hit (HTTP 429). Honoring retry_after={retry_after}s...")
                    time.sleep(retry_after)
                    continue

                return (status_code, parsed_json, body_text)

            except Exception as e:
                logger.warning(f"[Telegram] Request attempt {attempt}/{max_retries} failed: {e}")
                if attempt >= max_retries:
                    return (0, {}, str(e))
                time.sleep(backoff)
                backoff *= 2.0
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

        return (0, {}, "Max retries exceeded")
