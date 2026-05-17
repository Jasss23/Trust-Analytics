#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


class CDP:
    def __init__(self, ws_url: str) -> None:
        _, rest = ws_url.split("://", 1)
        host_port, path = rest.split("/", 1)
        host, port = host_port.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = self.sock.recv(4096)
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(response.decode(errors="replace"))
        self.next_id = 0

    def send(self, method: str, params: dict | None = None) -> int:
        self.next_id += 1
        payload = json.dumps({"id": self.next_id, "method": method, "params": params or {}}).encode()
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(header + masked)
        return self.next_id

    def recv(self) -> dict:
        first = self.sock.recv(2)
        if not first:
            raise EOFError
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.sock.recv(8))[0]
        if first[1] & 0x80:
            mask = self.sock.recv(4)
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(self.sock.recv(length)))
        else:
            payload = b""
            while len(payload) < length:
                payload += self.sock.recv(length - len(payload))
        return json.loads(payload.decode())

    def call(self, method: str, params: dict | None = None, timeout: float = 5) -> dict:
        message_id = self.send(method, params)
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self.recv()
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message
        raise TimeoutError(method)

    def close(self) -> None:
        self.sock.close()


def chrome_path() -> str:
    configured = os.environ.get("CHROME_PATH")
    if configured and Path(configured).exists():
        return configured
    for candidate in DEFAULT_CHROME_PATHS:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError("Set CHROME_PATH to a Chrome or Chromium executable.")


def wait_json(url: str, timeout: float = 8) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.loads(response.read())
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(url)


def wait_http(url: str, timeout: float = 8) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(f"App is not reachable: {url}")


def evaluate(cdp: CDP, expression: str, timeout: float = 15) -> dict:
    response = cdp.call(
        "Runtime.evaluate",
        {"expression": expression, "awaitPromise": True, "returnByValue": True},
        timeout=timeout,
    )
    result = response["result"]["result"]
    if "exceptionDetails" in response["result"]:
        raise RuntimeError(response["result"]["exceptionDetails"])
    if result.get("subtype") == "error":
        raise RuntimeError(result.get("description", "Browser evaluation failed"))
    return result.get("value")


def save_screenshot(cdp: CDP, target: Path, *, timeout: float = 10) -> None:
    response = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True}, timeout=timeout)
    data = base64.b64decode(response["result"]["data"])
    target.write_bytes(data)


def smoke_script() -> str:
    return r"""
    (async () => {
      const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
      const report = { results: [], errors: [] };
      window.addEventListener('error', event => report.errors.push(event.message));

      const clickByText = async (label, selector = 'button,a') => {
        const elements = [...document.querySelectorAll(selector)];
        const element = elements.find(node => (node.textContent || '').trim().includes(label));
        if (!element) {
          report.results.push({ label, ok: false, reason: 'missing' });
          return false;
        }
        const before = location.pathname;
        const disabled = !!element.disabled || element.getAttribute('aria-disabled') === 'true';
        element.click();
        await sleep(450);
        report.results.push({ label, ok: !disabled, before, after: location.pathname, disabled });
        return !disabled;
      };

      await clickByText('Examples');
      await sleep(500);
      report.libraryCards = document.querySelectorAll('.library-card').length;

      await clickByText('Ask new question');
      await sleep(500);
      await clickByText('Add period: October 2025');
      await clickByText('Set audience: Leadership');
      await clickByText('Ask for decision pack');
      report.smartAmendments = [...document.querySelectorAll('.suggestion-chip')]
        .map(node => (node.textContent || '').trim())
        .filter(Boolean);
      report.fieldStates = [...document.querySelectorAll('.field-state')]
        .map(node => (node.textContent || '').trim());
      report.askPathAfterAmendments = location.pathname;

      const confirmButtons = [...document.querySelectorAll('button')]
        .filter(button => (button.textContent || '').trim() === 'Confirm');
      for (const button of confirmButtons.slice(0, 6)) {
        button.click();
        await sleep(120);
      }

      const optionButtons = [...document.querySelectorAll('.inline-options.visible button')].slice(0, 6);
      for (const button of optionButtons) {
        const label = (button.textContent || '').trim();
        button.click();
        await sleep(120);
        report.results.push({ label: `inline:${label}`, ok: true, after: location.pathname });
      }

      await clickByText('Open evidence room');
      await sleep(500);
      report.evidencePath = location.pathname;

      await clickByText('Ask', 'a,button');
      await sleep(400);

      const textarea = document.querySelector('textarea.command-input');
      if (textarea) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
        setter.call(textarea, 'Can we rely on the December monthly summary GTV trend?');
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        await sleep(450);
        const chip = document.querySelector('.path-status-chip');
        report.auditChipTone = chip?.className.match(/tone-[a-z]+/)?.[0] || null;
        report.auditChipLabel = (chip?.textContent || '').trim();
      }

      await clickByText('Library', 'a,button');
      await sleep(500);
      await clickByText('Open pack');
      await sleep(600);
      report.packPath = location.pathname;
      report.packHeadline = document.querySelector('h1')?.textContent || '';

      await clickByText('Admin', 'a,button');
      await sleep(700);
      report.adminPath = location.pathname;
      report.adminTitle = document.querySelector('h1')?.textContent || '';
      report.adminRows = document.querySelectorAll('.cost-table tbody tr').length;

      await clickByText('Details');
      await sleep(500);
      report.costDetailOpen = !!document.querySelector('.cost-detail');
      report.buttonCount = document.querySelectorAll('button').length;
      report.finalPath = location.pathname;
      return report;
    })()
    """


def run_smoke(url: str, port: int, screenshot_dir: Path) -> dict:
    wait_http(url)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    chrome = subprocess.Popen(
        [
            chrome_path(),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--window-size=1440,1000",
            f"--remote-debugging-port={port}",
            "--user-data-dir=/private/tmp/trust-analytics-chrome-smoke",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    cdp: CDP | None = None
    try:
        pages = wait_json(f"http://127.0.0.1:{port}/json")
        ws_url = next(page["webSocketDebuggerUrl"] for page in pages if page.get("type") == "page")
        cdp = CDP(ws_url)
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        time.sleep(1)
        save_screenshot(cdp, screenshot_dir / "ask.png")
        result = evaluate(cdp, smoke_script(), timeout=20)
        save_screenshot(cdp, screenshot_dir / "admin-costs.png")
        evaluate(cdp, "location.href = '/library'; true", timeout=5)
        time.sleep(0.8)
        save_screenshot(cdp, screenshot_dir / "library.png")
        evaluate(cdp, "location.href = '/analysis/q1_gtv_idr_by_asset_oct_2025'; true", timeout=5)
        time.sleep(0.8)
        save_screenshot(cdp, screenshot_dir / "pack.png")
        evaluate(cdp, "location.href = '/review/q1_gtv_idr_by_asset_oct_2025'; true", timeout=5)
        time.sleep(0.8)
        save_screenshot(cdp, screenshot_dir / "evidence.png")
        evaluate(cdp, "location.href = '/handoff/q5_gtv_mom_trend_oct_dec_2025'; true", timeout=5)
        time.sleep(0.8)
        save_screenshot(cdp, screenshot_dir / "handoff.png")
        handoff_title = evaluate(cdp, "document.querySelector('.subpage-hero.blocked h1')?.textContent || ''", timeout=3)
        result["handoffTitle"] = handoff_title
        result["screenshots"] = {
            "ask": str(screenshot_dir / "ask.png"),
            "adminCosts": str(screenshot_dir / "admin-costs.png"),
            "library": str(screenshot_dir / "library.png"),
            "pack": str(screenshot_dir / "pack.png"),
            "evidence": str(screenshot_dir / "evidence.png"),
            "handoff": str(screenshot_dir / "handoff.png"),
        }
        missing = [item for item in result["results"] if not item.get("ok")]
        audit_chip_ok = (
            result.get("auditChipLabel") == "Audit required"
            and result.get("auditChipTone") in {"tone-critical", "tone-audit"}
        )
        if (
            result.get("errors")
            or missing
            or not result.get("costDetailOpen")
            or not audit_chip_ok
        ):
            result["ok"] = False
        else:
            result["ok"] = True
        return result
    finally:
        if cdp is not None:
            cdp.close()
        chrome.terminate()
        try:
            chrome.wait(timeout=2)
        except subprocess.TimeoutExpired:
            chrome.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Click through the Trust Analytics UI and save smoke screenshots.")
    parser.add_argument("--url", default="http://127.0.0.1:8080/", help="Running Trust Analytics app URL.")
    parser.add_argument("--port", type=int, default=9224, help="Chrome remote debugging port.")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=Path("/private/tmp/trust-analytics-smoke"),
        help="Directory where PNG screenshots are written.",
    )
    args = parser.parse_args()

    report = run_smoke(args.url, args.port, args.screenshot_dir)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
