#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
China Mobile home broadband diagnostic helper.

The script uses only Python's standard library plus OS tools such as ping and
traceroute. It does not change router, optical modem, or system settings.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import os
import platform
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


DEFAULT_IP_TARGETS = [
    ("AliDNS", "223.5.5.5"),
    ("TencentDNS", "119.29.29.29"),
    ("114DNS", "114.114.114.114"),
    ("BaiduDNS", "180.76.76.76"),
]

DEFAULT_DOMAINS = [
    "www.baidu.com",
    "www.qq.com",
    "www.10086.cn",
]

DEFAULT_URLS = [
    "https://www.baidu.com/",
    "https://www.qq.com/",
    "https://www.10086.cn/",
]

DNS_RESOLVERS = [
    ("AliDNS", "223.5.5.5"),
    ("TencentDNS", "119.29.29.29"),
    ("114DNS", "114.114.114.114"),
]


@dataclasses.dataclass
class CheckResult:
    category: str
    name: str
    status: str
    summary: str
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def utc_offset() -> str:
    now = dt.datetime.now().astimezone()
    offset = now.utcoffset()
    if offset is None:
        return ""
    seconds = int(offset.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"


def run_cmd(args: Sequence[str], timeout: int) -> Dict[str, Any]:
    env = os.environ.copy()
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    started = monotonic_ms()
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "elapsed_ms": monotonic_ms() - started,
            "command": list(args),
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "command not found",
            "elapsed_ms": monotonic_ms() - started,
            "command": list(args),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": "command timeout",
            "elapsed_ms": monotonic_ms() - started,
            "command": list(args),
        }


def get_local_ip() -> Optional[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(1)
        sock.connect(("223.5.5.5", 53))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def get_default_gateway() -> Tuple[Optional[str], Dict[str, Any]]:
    system = platform.system().lower()
    if system == "darwin":
        out = run_cmd(["route", "-n", "get", "default"], timeout=5)
        match = re.search(r"gateway:\s*([0-9.]+)", out.get("stdout", ""))
        return (match.group(1) if match else None, out)
    if system == "linux":
        out = run_cmd(["ip", "-4", "route", "show", "default"], timeout=5)
        match = re.search(r"default\s+via\s+([0-9.]+)", out.get("stdout", ""))
        return (match.group(1) if match else None, out)
    if system == "windows":
        out = run_cmd(["route", "print", "-4"], timeout=8)
        for line in out.get("stdout", "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                return parts[2], out
        return None, out
    return None, {"ok": False, "stderr": f"unsupported platform: {platform.system()}"}


def ping_command(target: str, count: int, timeout_ms: int) -> Optional[List[str]]:
    if shutil.which("ping") is None:
        return None
    system = platform.system().lower()
    count = max(1, min(count, 20))
    timeout_ms = max(500, timeout_ms)
    if system == "darwin":
        return ["ping", "-n", "-c", str(count), "-W", str(timeout_ms), target]
    if system == "linux":
        return [
            "ping",
            "-n",
            "-c",
            str(count),
            "-W",
            str(max(1, int(math.ceil(timeout_ms / 1000)))),
            target,
        ]
    if system == "windows":
        return ["ping", "-n", str(count), "-w", str(timeout_ms), target]
    return ["ping", "-c", str(count), target]


def parse_ping_output(output: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    loss_match = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s*packet loss", output)
    if not loss_match:
        loss_match = re.search(r"\(([0-9]+(?:\.[0-9]+)?)%\s*loss\)", output)
    if loss_match:
        result["packet_loss_percent"] = float(loss_match.group(1))

    transmitted = re.search(r"([0-9]+)\s+packets transmitted,\s+([0-9]+)\s+(?:packets )?received", output)
    if transmitted:
        result["sent"] = int(transmitted.group(1))
        result["received"] = int(transmitted.group(2))
    else:
        win_packets = re.search(r"Sent\s*=\s*([0-9]+),\s*Received\s*=\s*([0-9]+),\s*Lost", output)
        if win_packets:
            result["sent"] = int(win_packets.group(1))
            result["received"] = int(win_packets.group(2))

    avg_match = re.search(r"(?:round-trip|rtt)[^=]*=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)", output)
    if avg_match:
        result["min_ms"] = float(avg_match.group(1))
        result["avg_ms"] = float(avg_match.group(2))
        result["max_ms"] = float(avg_match.group(3))
    else:
        win_avg = re.search(r"Average\s*=\s*([0-9]+)ms", output)
        if win_avg:
            result["avg_ms"] = float(win_avg.group(1))
    return result


def ping_test(category: str, name: str, target: str, count: int, timeout_ms: int) -> CheckResult:
    command = ping_command(target, count, timeout_ms)
    if command is None:
        return CheckResult(category, name, "info", "系统未找到 ping 命令，跳过 ICMP 检测。")

    timeout = max(3, int(math.ceil((timeout_ms * count) / 1000)) + 2)
    out = run_cmd(command, timeout=timeout)
    parsed = parse_ping_output(out.get("stdout", "") + "\n" + out.get("stderr", ""))
    details = {"target": target, "parsed": parsed, "raw": out}
    loss = parsed.get("packet_loss_percent")
    avg = parsed.get("avg_ms")

    if out["ok"] and loss is not None and loss <= 0:
        latency = f"，平均 {avg:.1f} ms" if isinstance(avg, (int, float)) else ""
        return CheckResult(category, name, "ok", f"可达，0% 丢包{latency}。", details)
    if loss is not None and loss < 100:
        latency = f"，平均 {avg:.1f} ms" if isinstance(avg, (int, float)) else ""
        return CheckResult(category, name, "warn", f"可达但丢包 {loss:.1f}%{latency}。", details)
    if out["ok"]:
        return CheckResult(category, name, "ok", "ping 返回成功，但未能解析丢包率。", details)
    return CheckResult(category, name, "fail", "不可达或全部超时。", details)


def tcp_connect_test(category: str, name: str, host: str, port: int, timeout_sec: float) -> CheckResult:
    started = monotonic_ms()
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            elapsed = monotonic_ms() - started
            return CheckResult(
                category,
                name,
                "ok",
                f"TCP {host}:{port} 连接成功，耗时 {elapsed} ms。",
                {"host": host, "port": port, "elapsed_ms": elapsed},
            )
    except OSError as exc:
        elapsed = monotonic_ms() - started
        return CheckResult(
            category,
            name,
            "fail",
            f"TCP {host}:{port} 连接失败：{exc}",
            {"host": host, "port": port, "elapsed_ms": elapsed, "error": repr(exc)},
        )


def dns_encode_name(name: str) -> bytes:
    parts = name.rstrip(".").split(".")
    encoded = b""
    for part in parts:
        label = part.encode("idna")
        if len(label) > 63:
            raise ValueError(f"DNS label too long: {part}")
        encoded += bytes([len(label)]) + label
    return encoded + b"\x00"


def dns_read_name(data: bytes, offset: int) -> Tuple[str, int]:
    labels: List[str] = []
    jumped = False
    original_next = offset
    jumps = 0
    while True:
        if offset >= len(data):
            raise ValueError("DNS name offset out of range")
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("DNS compression pointer truncated")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                original_next = offset + 2
            offset = pointer
            jumped = True
            jumps += 1
            if jumps > 20:
                raise ValueError("too many DNS compression jumps")
            continue
        if length == 0:
            offset += 1
            break
        offset += 1
        labels.append(data[offset : offset + length].decode("ascii", errors="replace"))
        offset += length
    return ".".join(labels), original_next if jumped else offset


def dns_query_a(server: str, domain: str, timeout_sec: float) -> Tuple[List[str], int]:
    txid = random.randint(0, 65535)
    header = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    question = dns_encode_name(domain) + struct.pack("!HH", 1, 1)
    packet = header + question

    started = monotonic_ms()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout_sec)
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(2048)
    finally:
        sock.close()
    elapsed = monotonic_ms() - started

    if len(data) < 12:
        raise ValueError("DNS response too short")
    rxid, flags, _qdcount, ancount, _nscount, _arcount = struct.unpack("!HHHHHH", data[:12])
    if rxid != txid:
        raise ValueError("DNS transaction id mismatch")
    rcode = flags & 0x000F
    if rcode != 0:
        raise ValueError(f"DNS rcode={rcode}")

    offset = 12
    for _ in range(_qdcount):
        _, offset = dns_read_name(data, offset)
        offset += 4

    ips: List[str] = []
    for _ in range(ancount):
        _, offset = dns_read_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError("DNS answer truncated")
        rr_type, rr_class, _ttl, rdlength = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlength]
        offset += rdlength
        if rr_type == 1 and rr_class == 1 and rdlength == 4:
            ips.append(socket.inet_ntoa(rdata))
    return ips, elapsed


def dns_server_test(resolver_name: str, server: str, domain: str, timeout_sec: float) -> CheckResult:
    try:
        ips, elapsed = dns_query_a(server, domain, timeout_sec)
        if ips:
            return CheckResult(
                "dns",
                f"{resolver_name} {server}",
                "ok",
                f"{domain} 解析成功，{elapsed} ms，返回 {', '.join(ips[:4])}。",
                {"server": server, "domain": domain, "ips": ips, "elapsed_ms": elapsed},
            )
        return CheckResult(
            "dns",
            f"{resolver_name} {server}",
            "warn",
            f"{domain} 有响应但没有 A 记录。",
            {"server": server, "domain": domain, "ips": [], "elapsed_ms": elapsed},
        )
    except Exception as exc:
        return CheckResult(
            "dns",
            f"{resolver_name} {server}",
            "fail",
            f"{domain} 解析失败：{exc}",
            {"server": server, "domain": domain, "error": repr(exc)},
        )


def dns_system_test(domain: str, timeout_sec: float) -> CheckResult:
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_sec)
    started = monotonic_ms()
    try:
        infos = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
        elapsed = monotonic_ms() - started
        ips = sorted({item[4][0] for item in infos})
        return CheckResult(
            "dns",
            f"system {domain}",
            "ok",
            f"系统 DNS 解析成功，{elapsed} ms，返回 {', '.join(ips[:4])}。",
            {"domain": domain, "ips": ips, "elapsed_ms": elapsed},
        )
    except OSError as exc:
        elapsed = monotonic_ms() - started
        return CheckResult(
            "dns",
            f"system {domain}",
            "fail",
            f"系统 DNS 解析失败：{exc}",
            {"domain": domain, "elapsed_ms": elapsed, "error": repr(exc)},
        )
    finally:
        socket.setdefaulttimeout(previous_timeout)


def http_test(url: str, timeout_sec: float) -> CheckResult:
    started = monotonic_ms()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "cmcc-broadband-diag/1.0",
            "Range": "bytes=0-2047",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = response.read(2048)
            elapsed = monotonic_ms() - started
            code = getattr(response, "status", response.getcode())
            status = "ok" if 200 <= code < 400 else "warn"
            return CheckResult(
                "http",
                url,
                status,
                f"HTTP {code}，读取 {len(body)} 字节，耗时 {elapsed} ms。",
                {"url": url, "status_code": code, "bytes": len(body), "elapsed_ms": elapsed},
            )
    except urllib.error.HTTPError as exc:
        elapsed = monotonic_ms() - started
        status = "warn" if 400 <= exc.code < 500 else "fail"
        return CheckResult(
            "http",
            url,
            status,
            f"HTTP {exc.code}：{exc.reason}",
            {"url": url, "status_code": exc.code, "elapsed_ms": elapsed, "error": repr(exc)},
        )
    except Exception as exc:
        elapsed = monotonic_ms() - started
        return CheckResult(
            "http",
            url,
            "fail",
            f"访问失败：{exc}",
            {"url": url, "elapsed_ms": elapsed, "error": repr(exc)},
        )


def traceroute_test(target: str) -> CheckResult:
    system = platform.system().lower()
    if system == "windows":
        command = ["tracert", "-d", "-h", "12", "-w", "2000", target]
    elif shutil.which("traceroute"):
        command = ["traceroute", "-n", "-w", "2", "-q", "1", "-m", "12", target]
    elif shutil.which("tracepath"):
        command = ["tracepath", "-n", "-m", "12", target]
    else:
        return CheckResult("route", target, "info", "系统未找到 traceroute/tracepath，跳过路由跟踪。")

    out = run_cmd(command, timeout=35)
    status = "ok" if out.get("ok") else "warn"
    summary = "路由跟踪完成。" if out.get("ok") else "路由跟踪未完整完成，仍可查看原始输出。"
    return CheckResult("route", target, status, summary, {"target": target, "raw": out})


def normalize_to_mbps(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit == "gbps":
        return value * 1000
    if unit == "kbps":
        return value / 1000
    return value


def parse_network_quality_output(output: str) -> Dict[str, Any]:
    parsed: Dict[str, Any] = {}
    patterns = {
        "download_mbps": r"(?:Download|Downlink)\s+capacity\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*([KMG]bps)",
        "upload_mbps": r"(?:Upload|Uplink)\s+capacity\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*([KMG]bps)",
        "idle_latency_ms": r"Idle Latency\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*(?:ms|milliseconds)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if not match:
            continue
        if key in ("download_mbps", "upload_mbps"):
            parsed[key] = normalize_to_mbps(float(match.group(1)), match.group(2))
        elif key == "idle_latency_ms":
            parsed[key] = float(match.group(1))

    responsiveness_pattern = re.compile(
        r"(?:(Uplink|Downlink)\s+)?Responsiveness\s*:\s*([A-Za-z]+)\s*"
        r"\((?:[^|)]*\|\s*)?([0-9]+)\s*RPM\)"
    )
    for match in responsiveness_pattern.finditer(output):
        direction = match.group(1)
        label = match.group(2)
        rpm = int(match.group(3))
        if direction == "Downlink":
            parsed["download_responsiveness_label"] = label
            parsed["download_responsiveness_rpm"] = rpm
        elif direction == "Uplink":
            parsed["upload_responsiveness_label"] = label
            parsed["upload_responsiveness_rpm"] = rpm
        else:
            parsed["responsiveness_label"] = label
            parsed["responsiveness_rpm"] = rpm
    return parsed


def load_json_from_output(output: str) -> Dict[str, Any]:
    text = output.strip()
    if not text:
        raise ValueError("empty JSON output")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("speedtest JSON root is not an object")
    return data


def parse_ookla_speedtest_json(output: str) -> Dict[str, Any]:
    data = load_json_from_output(output)
    parsed: Dict[str, Any] = {}

    download = data.get("download")
    if isinstance(download, dict) and isinstance(download.get("bandwidth"), (int, float)):
        parsed["download_mbps"] = float(download["bandwidth"]) * 8 / 1_000_000

    upload = data.get("upload")
    if isinstance(upload, dict) and isinstance(upload.get("bandwidth"), (int, float)):
        parsed["upload_mbps"] = float(upload["bandwidth"]) * 8 / 1_000_000

    ping = data.get("ping")
    if isinstance(ping, dict):
        if isinstance(ping.get("latency"), (int, float)):
            parsed["idle_latency_ms"] = float(ping["latency"])
        if isinstance(ping.get("jitter"), (int, float)):
            parsed["jitter_ms"] = float(ping["jitter"])

    server = data.get("server")
    if isinstance(server, dict):
        labels = [
            str(server[key])
            for key in ("name", "location", "country")
            if server.get(key)
        ]
        if labels:
            parsed["server"] = " / ".join(labels)

    result = data.get("result")
    if isinstance(result, dict) and result.get("url"):
        parsed["result_url"] = str(result["url"])
    return parsed


def parse_speedtest_cli_json(output: str) -> Dict[str, Any]:
    data = load_json_from_output(output)
    parsed: Dict[str, Any] = {}

    if isinstance(data.get("download"), (int, float)):
        parsed["download_mbps"] = float(data["download"]) / 1_000_000
    if isinstance(data.get("upload"), (int, float)):
        parsed["upload_mbps"] = float(data["upload"]) / 1_000_000
    if isinstance(data.get("ping"), (int, float)):
        parsed["idle_latency_ms"] = float(data["ping"])

    server = data.get("server")
    if isinstance(server, dict):
        labels = [
            str(server[key])
            for key in ("sponsor", "name", "country")
            if server.get(key)
        ]
        if labels:
            parsed["server"] = " / ".join(labels)
    return parsed


def speedtest_command_kind(command: str) -> str:
    out = run_cmd([command, "--help"], timeout=5)
    text = (out.get("stdout", "") + "\n" + out.get("stderr", "")).lower()
    if "ookla" in text or ("--format" in text and "--accept-license" in text):
        return "ookla"
    if "speedtest-cli" in text or "--json" in text:
        return "speedtest-cli"
    return "unknown"


def speedtest_report(
    metadata: Dict[str, Any],
    status: str,
    parsed: Dict[str, Any],
    raw: Dict[str, Any],
    notes: Sequence[str],
) -> Dict[str, Any]:
    return {
        "metadata": metadata,
        "status": status,
        "parsed": parsed,
        "raw": raw,
        "notes": list(notes),
    }


def has_speedtest_value(parsed: Dict[str, Any], key: str) -> bool:
    return isinstance(parsed.get(key), (int, float))


def speedtest_is_complete(parsed: Dict[str, Any]) -> bool:
    return has_speedtest_value(parsed, "download_mbps") and has_speedtest_value(parsed, "upload_mbps")


def network_quality_notes(raw: Dict[str, Any], parsed: Dict[str, Any]) -> List[str]:
    notes = [
        "macOS networkQuality 使用 Apple 测速服务，结果适合判断家庭宽带实际下载/上载能力。",
        "测速时请暂停网盘、视频、游戏更新和其他大流量任务；最好用网线直连路由器或光猫 LAN 口。",
    ]
    missing = []
    if not has_speedtest_value(parsed, "download_mbps"):
        missing.append("下载")
    if not has_speedtest_value(parsed, "upload_mbps"):
        missing.append("上载")
    if missing:
        notes.insert(
            0,
            f"本次 networkQuality 未测得{'、'.join(missing)}速度，报告属于不完整测速；不要只用这一次结果判断完整带宽。",
        )

    raw_text = "\n".join(str(raw.get(key, "")) for key in ("stdout", "stderr"))
    if re.search(r"TLS error|NSURLErrorDomain Code=-1200|-9816", raw_text, re.IGNORECASE):
        notes.insert(
            0,
            "networkQuality 返回 TLS 错误，Apple 测速服务连接未完整完成；如正在使用代理/VPN/安全软件，建议关闭后重测并交叉验证。",
        )
    elif not raw.get("ok"):
        first_line = next(
            (
                line.strip()
                for line in raw_text.splitlines()
                if line.strip() and not line.strip().startswith("====")
            ),
            "",
        )
        if first_line:
            notes.insert(0, f"networkQuality 返回错误：{first_line}")
    return notes


def run_network_quality_speedtest(max_runtime_sec: int, metadata: Dict[str, Any]) -> Dict[str, Any]:
    command = ["networkQuality", "-s", "-M", str(max_runtime_sec)]
    raw = run_cmd(command, timeout=max_runtime_sec + 45)
    parsed = parse_network_quality_output(raw.get("stdout", "") + "\n" + raw.get("stderr", ""))
    status = "ok" if raw.get("ok") and speedtest_is_complete(parsed) else "warn"
    metadata["tool"] = "networkQuality"
    return speedtest_report(metadata, status, parsed, raw, network_quality_notes(raw, parsed))


def run_ookla_speedtest(command: str, max_runtime_sec: int, metadata: Dict[str, Any]) -> Dict[str, Any]:
    raw = run_cmd(
        [command, "--accept-license", "--accept-gdpr", "--format=json"],
        timeout=max_runtime_sec + 45,
    )
    parsed: Dict[str, Any] = {}
    parse_error = None
    if raw.get("stdout"):
        try:
            parsed = parse_ookla_speedtest_json(raw["stdout"])
        except Exception as exc:
            parse_error = repr(exc)
            raw["parse_error"] = parse_error
    status = "ok" if raw.get("ok") and speedtest_is_complete(parsed) else "warn"
    metadata["tool"] = "Ookla speedtest"
    return speedtest_report(
        metadata,
        status,
        parsed,
        raw,
        [
            "Linux/Rocky Linux 使用 Ookla speedtest CLI 时，脚本会读取 JSON 输出并换算为 Mbps 与 MB/s。",
            "首次运行可能需要接受 Ookla 许可；脚本已传入 --accept-license 和 --accept-gdpr。",
            "测速时请暂停网盘、视频、游戏更新和其他大流量任务；最好用网线直连路由器或光猫 LAN 口。",
        ],
    )


def run_speedtest_cli(command: str, max_runtime_sec: int, metadata: Dict[str, Any]) -> Dict[str, Any]:
    raw = run_cmd([command, "--secure", "--json"], timeout=max_runtime_sec + 45)
    parsed: Dict[str, Any] = {}
    if raw.get("stdout"):
        try:
            parsed = parse_speedtest_cli_json(raw["stdout"])
        except Exception as exc:
            raw["parse_error"] = repr(exc)
    status = "ok" if raw.get("ok") and speedtest_is_complete(parsed) else "warn"
    metadata["tool"] = "speedtest-cli"
    return speedtest_report(
        metadata,
        status,
        parsed,
        raw,
        [
            "Linux/Rocky Linux 使用 speedtest-cli 时，脚本会通过 HTTPS 获取配置，读取 JSON 输出并换算为 Mbps 与 MB/s。",
            "speedtest-cli 会自动选择测速服务器，结果适合粗略判断家庭宽带下载/上载能力。",
            "测速时请暂停网盘、视频、游戏更新和其他大流量任务；最好用网线直连路由器或光猫 LAN 口。",
        ],
    )


def run_cli_speedtest_tool(max_runtime_sec: int, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    speedtest = shutil.which("speedtest")
    if speedtest:
        kind = speedtest_command_kind(speedtest)
        if kind == "ookla":
            return run_ookla_speedtest(speedtest, max_runtime_sec, metadata)
        if kind == "speedtest-cli":
            return run_speedtest_cli(speedtest, max_runtime_sec, metadata)

        report = run_ookla_speedtest(speedtest, max_runtime_sec, metadata)
        if report.get("raw", {}).get("ok") or report.get("parsed"):
            return report
        return run_speedtest_cli(speedtest, max_runtime_sec, metadata)

    speedtest_cli = shutil.which("speedtest-cli")
    if speedtest_cli:
        return run_speedtest_cli(speedtest_cli, max_runtime_sec, metadata)
    return None


def run_speedtest(max_runtime_sec: int) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "utc_offset": utc_offset(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "tool": None,
    }
    max_runtime_sec = max(10, min(max_runtime_sec, 180))

    if platform.system().lower() == "darwin" and shutil.which("networkQuality"):
        network_quality_report = run_network_quality_speedtest(max_runtime_sec, metadata.copy())
        if network_quality_report["status"] == "ok":
            return network_quality_report

        fallback_report = run_cli_speedtest_tool(max_runtime_sec, metadata.copy())
        if fallback_report is not None:
            fallback_tool = fallback_report["metadata"].get("tool") or "备用测速工具"
            fallback_report["metadata"]["tool"] = f"networkQuality -> {fallback_tool}"
            fallback_report["raw"]["previous_networkQuality"] = network_quality_report["raw"]
            fallback_report["raw"]["previous_networkQuality_parsed"] = network_quality_report["parsed"]
            fallback_report["notes"].insert(
                0,
                "networkQuality 未完整完成，脚本已自动改用备用测速工具；JSON 中保留前一次 networkQuality 原始输出。",
            )
            return fallback_report

        network_quality_report["notes"].insert(
            0,
            "本机未找到 speedtest/speedtest-cli 备用工具；本次报告只包含 networkQuality 的部分结果。",
        )
        return network_quality_report

    cli_report = run_cli_speedtest_tool(max_runtime_sec, metadata)
    if cli_report is not None:
        return cli_report

    return {
        "metadata": metadata,
        "status": "fail",
        "parsed": {},
        "raw": {"ok": False, "stderr": "no supported speedtest tool found"},
        "notes": [
            "当前系统没有可用测速工具。macOS 可用 networkQuality；Rocky Linux/Linux 可安装 Ookla speedtest CLI 或 speedtest-cli。",
            "Rocky Linux 上可先检查命令是否存在：command -v speedtest 或 command -v speedtest-cli。",
            "上载测速必须有远端服务器接收数据；单靠 ping、DNS 或普通网页下载不能准确测上载带宽。",
        ],
    }


def status_rank(status: str) -> int:
    return {"ok": 0, "info": 1, "warn": 2, "fail": 3}.get(status, 1)


def count_by_status(results: Sequence[CheckResult], category: str) -> Dict[str, int]:
    subset = [item for item in results if item.category == category]
    return {
        "total": len(subset),
        "ok": sum(1 for item in subset if item.status == "ok"),
        "warn": sum(1 for item in subset if item.status == "warn"),
        "fail": sum(1 for item in subset if item.status == "fail"),
        "info": sum(1 for item in subset if item.status == "info"),
    }


def public_ip_results(results: Sequence[CheckResult]) -> List[CheckResult]:
    return [item for item in results if item.category == "wan_ping"]


def classify(results: Sequence[CheckResult], gateway: Optional[str], app_group_fault: bool) -> Dict[str, Any]:
    gateway_result = next((item for item in results if item.category == "lan"), None)
    pings = public_ip_results(results)
    dns_system = [item for item in results if item.category == "dns" and item.name.startswith("system ")]
    http = [item for item in results if item.category == "http"]
    tcp = [item for item in results if item.category == "tcp"]

    public_ok = sum(1 for item in pings if item.status == "ok")
    public_fail = sum(1 for item in pings if item.status == "fail")
    tcp_ok = sum(1 for item in tcp if item.status == "ok")
    dns_system_ok = sum(1 for item in dns_system if item.status == "ok")
    http_ok = sum(1 for item in http if item.status == "ok")

    reasons: List[str] = []
    next_steps: List[str] = []
    primary = "需要结合报告判断"
    severity = "warn"

    if app_group_fault:
        reasons.append("中国移动 App 已提示“存在群障/线路状态异常”，这通常是运营商接入网、OLT、分光、光缆或区域设备侧问题。")

    operation_not_permitted = any(
        "Operation not permitted" in str(item.details) or "Errno 1" in str(item.details)
        for item in results
    )
    gateway_really_bad = gateway_result and gateway_result.status == "fail"
    gateway_unknown = gateway_result and gateway_result.status == "warn" and not gateway

    if operation_not_permitted:
        primary = "当前运行环境限制了网络探测，建议在普通终端重新运行"
        severity = "warn"
        reasons.append("检测结果里出现 Operation not permitted，这常见于沙箱或系统权限限制，不能直接当作宽带故障证据。")
        next_steps.extend(
            [
                "在 macOS 终端或 Windows PowerShell 里直接运行脚本，不要在受限沙箱里运行。",
                "重新运行：python3 cmcc_broadband_diag.py --app-group-fault --deep",
            ]
        )
    elif gateway_really_bad:
        primary = "优先排查家里 Wi-Fi/路由器/光猫到终端这一段"
        severity = "fail"
        reasons.append(f"默认网关 {gateway or ''} 检测异常：{gateway_result.summary}")
        next_steps.extend(
            [
                "用网线直连路由器 LAN 口再测一次，避免 Wi-Fi 干扰误判。",
                "重启路由器和光猫：断电 30 秒后先开光猫，PON/Internet 稳定后再开路由器。",
                "检查光猫 LOS 是否红灯、PON 是否闪烁异常，检查光纤是否弯折或松动。",
            ]
        )
    elif pings and public_fail == len(pings) and tcp_ok == 0:
        primary = "高度疑似运营商 WAN/线路侧故障"
        severity = "fail"
        if gateway_unknown:
            reasons.append("未能自动识别默认网关；同时多个公网 IP/TCP 目标都不可达，仍然更像 WAN、DNS、系统网络或运营商侧异常，需要在普通终端复测确认。")
        else:
            reasons.append("默认网关正常但多个公网 IP/TCP 目标都不可达，问题更像出在光猫上联、宽带账号、接入网或运营商出口。")
        next_steps.extend(
            [
                "如果光猫 LOS 红灯或 PON 长时间闪烁，直接向 10086 报“光路/线路状态异常”。",
                "向移动客服说明：App 显示群障，且本地网关可达但公网多目标不可达；要求查询小区/OLT 群障、光衰和端口状态。",
                "保留本脚本报告和智修精灵截图，催单时给装维师傅看。",
            ]
        )
    elif public_ok >= 2 and dns_system and dns_system_ok == 0:
        primary = "疑似 DNS 故障"
        severity = "warn"
        reasons.append("公网 IP 连通性基本正常，但系统 DNS 解析失败。")
        next_steps.extend(
            [
                "先把路由器或电脑 DNS 临时改成 223.5.5.5、119.29.29.29 或 114.114.114.114 再测。",
                "如果手动 DNS 正常、自动 DNS 异常，向移动反馈“宽带下发 DNS 异常”。",
            ]
        )
    elif public_ok >= 2 and dns_system_ok >= 1 and http and http_ok == 0:
        primary = "疑似 HTTP/HTTPS 访问层异常"
        severity = "warn"
        reasons.append("IP 和 DNS 大体正常，但网页访问失败，可能是透明代理、TLS、劫持页、IPv6/IPv4 或特定出口异常。")
        next_steps.extend(
            [
                "关闭代理/VPN/加速器后再测一次。",
                "分别测试手机流量和家里宽带，确认是否只在移动宽带上复现。",
                "把报告中的 HTTP 失败项发给运营商，要求检查出口访问异常。",
            ]
        )
    elif public_ok >= 2 and dns_system_ok >= 1 and (not http or http_ok >= 1):
        primary = "本次基础连通性基本正常"
        severity = "ok"
        reasons.append("默认网关、公网 IP、DNS 或 HTTP 至少有多项正常。")
        next_steps.extend(
            [
                "如果 App 仍显示群障，以 App 群障为准继续报修；群障可能是间歇性或影响部分目的地址。",
                "在故障发生时重新运行一次，并加 --deep 生成路由跟踪证据。",
            ]
        )
    else:
        if gateway_unknown:
            reasons.append("脚本未能自动识别默认网关，因此无法完整判断本地网关到终端这一段。")
        reasons.append("检测结果不够集中，可能是间歇性丢包、部分路由异常或测试目标被限制。")
        next_steps.extend(
            [
                "故障复现时运行：python3 cmcc_broadband_diag.py --app-group-fault --deep",
                "同时用手机连接同一 Wi-Fi 和电脑网线各测一次，比较是否只有某个终端异常。",
            ]
        )

    if app_group_fault and severity != "ok" and "10086" not in " ".join(next_steps):
        next_steps.append("向 10086 报修时明确说：智修精灵提示“存在群障/线路状态异常”，请核查区域群障和光路。")

    return {
        "primary": primary,
        "severity": severity,
        "reasons": reasons,
        "next_steps": dedupe(next_steps),
        "counts": {
            "lan": count_by_status(results, "lan"),
            "wan_ping": count_by_status(results, "wan_ping"),
            "tcp": count_by_status(results, "tcp"),
            "dns": count_by_status(results, "dns"),
            "http": count_by_status(results, "http"),
            "route": count_by_status(results, "route"),
        },
    }


def dedupe(items: Sequence[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def collect_diagnostics(args: argparse.Namespace) -> Dict[str, Any]:
    gateway, gateway_raw = get_default_gateway()
    local_ip = get_local_ip()
    metadata: Dict[str, Any] = {
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "utc_offset": utc_offset(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "local_ip": local_ip,
        "default_gateway": gateway,
        "app_group_fault": args.app_group_fault,
        "deep": args.deep,
    }

    results: List[CheckResult] = [
        CheckResult(
            "local",
            "environment",
            "info",
            f"本机 IP：{local_ip or '未获取'}；默认网关：{gateway or '未获取'}。",
            {"gateway_probe": gateway_raw},
        )
    ]

    if gateway:
        results.append(ping_test("lan", f"gateway {gateway}", gateway, args.count, args.timeout_ms))
    else:
        results.append(CheckResult("lan", "gateway", "warn", "未能自动识别默认网关，跳过网关 ping。"))

    for label, target in DEFAULT_IP_TARGETS:
        results.append(ping_test("wan_ping", f"{label} {target}", target, args.count, args.timeout_ms))

    results.append(tcp_connect_test("tcp", "AliDNS 53", "223.5.5.5", 53, args.timeout_ms / 1000))
    for domain in DEFAULT_DOMAINS:
        results.append(tcp_connect_test("tcp", f"{domain}:443", domain, 443, args.timeout_ms / 1000))

    for domain in DEFAULT_DOMAINS:
        results.append(dns_system_test(domain, args.timeout_ms / 1000))
    for resolver_name, resolver_ip in DNS_RESOLVERS:
        results.append(dns_server_test(resolver_name, resolver_ip, "www.baidu.com", args.timeout_ms / 1000))

    for url in DEFAULT_URLS:
        results.append(http_test(url, max(3, args.timeout_ms / 1000)))

    if args.deep:
        results.append(traceroute_test("223.5.5.5"))
        results.append(traceroute_test("119.29.29.29"))

    assessment = classify(results, gateway, args.app_group_fault)
    return {
        "metadata": metadata,
        "assessment": assessment,
        "results": [dataclasses.asdict(item) for item in results],
    }


def mark(status: str) -> str:
    return {
        "ok": "[OK]",
        "warn": "[WARN]",
        "fail": "[FAIL]",
        "info": "[INFO]",
    }.get(status, "[INFO]")


def render_text_report(report: Dict[str, Any]) -> str:
    metadata = report["metadata"]
    assessment = report["assessment"]
    lines: List[str] = []
    lines.append("中国移动家庭宽带诊断报告")
    lines.append("=" * 34)
    lines.append(f"生成时间: {metadata['created_at']} (UTC{metadata.get('utc_offset', '')})")
    lines.append(f"系统环境: {metadata['platform']} / Python {metadata['python']}")
    lines.append(f"本机 IP: {metadata.get('local_ip') or '未获取'}")
    lines.append(f"默认网关: {metadata.get('default_gateway') or '未获取'}")
    lines.append(f"已标记移动 App 群障: {'是' if metadata.get('app_group_fault') else '否'}")
    lines.append("")
    lines.append("结论")
    lines.append("-" * 34)
    lines.append(f"{mark(assessment['severity'])} {assessment['primary']}")
    for reason in assessment["reasons"]:
        lines.append(f"- {reason}")
    lines.append("")
    lines.append("建议动作")
    lines.append("-" * 34)
    for idx, step in enumerate(assessment["next_steps"], 1):
        lines.append(f"{idx}. {step}")
    lines.append("")
    lines.append("检测明细")
    lines.append("-" * 34)
    for item in report["results"]:
        lines.append(f"{mark(item['status'])} {item['category']} / {item['name']}: {item['summary']}")
        if item["category"] == "route" and item.get("details", {}).get("raw"):
            raw = item["details"]["raw"]
            stdout = raw.get("stdout", "").strip()
            stderr = raw.get("stderr", "").strip()
            if stdout:
                lines.append("  traceroute stdout:")
                lines.extend("    " + line for line in stdout.splitlines()[:30])
            if stderr:
                lines.append("  traceroute stderr:")
                lines.extend("    " + line for line in stderr.splitlines()[:10])
    lines.append("")
    lines.append("给 10086/装维师傅的简短描述")
    lines.append("-" * 34)
    if metadata.get("app_group_fault"):
        lines.append("中国移动 App 智修精灵提示“存在群障/线路状态异常”。")
    lines.append(f"本机默认网关: {metadata.get('default_gateway') or '未获取'}；脚本结论: {assessment['primary']}。")
    lines.append("请核查该宽带账号的 OLT/端口状态、光衰、PON 注册、区域群障和上联出口。")
    return "\n".join(lines) + "\n"


def render_speedtest_report(report: Dict[str, Any]) -> str:
    metadata = report["metadata"]
    parsed = report.get("parsed", {})
    lines: List[str] = []
    lines.append("家庭宽带下载/上载测速报告")
    lines.append("=" * 34)
    lines.append(f"生成时间: {metadata['created_at']} (UTC{metadata.get('utc_offset', '')})")
    lines.append(f"系统环境: {metadata['platform']} / Python {metadata['python']}")
    lines.append(f"测速工具: {metadata.get('tool') or '未找到可用工具'}")
    status_text = {"ok": "完整", "warn": "不完整/需复测", "fail": "失败"}.get(report.get("status"), "未知")
    lines.append(f"测速状态: {status_text}")
    lines.append("")

    download = parsed.get("download_mbps")
    upload = parsed.get("upload_mbps")
    if isinstance(download, (int, float)):
        lines.append(f"下载速度: {download:.2f} Mbps ({download / 8:.2f} MB/s)")
    else:
        lines.append("下载速度: 未测得")
    if isinstance(upload, (int, float)):
        lines.append(f"上载速度: {upload:.2f} Mbps ({upload / 8:.2f} MB/s)")
    else:
        lines.append("上载速度: 未测得")

    if isinstance(parsed.get("idle_latency_ms"), (int, float)):
        lines.append(f"空闲延迟: {parsed['idle_latency_ms']:.1f} ms")
    if isinstance(parsed.get("jitter_ms"), (int, float)):
        lines.append(f"抖动: {parsed['jitter_ms']:.1f} ms")
    if parsed.get("responsiveness_label") and isinstance(parsed.get("responsiveness_rpm"), int):
        lines.append(f"响应性: {parsed['responsiveness_label']} ({parsed['responsiveness_rpm']} RPM)")
    if parsed.get("download_responsiveness_label") and isinstance(parsed.get("download_responsiveness_rpm"), int):
        lines.append(
            f"下载响应性: {parsed['download_responsiveness_label']} ({parsed['download_responsiveness_rpm']} RPM)"
        )
    if parsed.get("upload_responsiveness_label") and isinstance(parsed.get("upload_responsiveness_rpm"), int):
        lines.append(
            f"上载响应性: {parsed['upload_responsiveness_label']} ({parsed['upload_responsiveness_rpm']} RPM)"
        )
    if parsed.get("server"):
        lines.append(f"测速服务器: {parsed['server']}")
    if parsed.get("result_url"):
        lines.append(f"结果链接: {parsed['result_url']}")

    lines.append("")
    lines.append("说明")
    lines.append("-" * 34)
    for note in report.get("notes", []):
        lines.append(f"- {note}")
    lines.append("- 运营商套餐通常按 Mbps 标注；下载软件常按 MB/s 显示。换算关系：1 MB/s = 8 Mbps。")
    lines.append("- 例如 300M 宽带理论下载约 37.5 MB/s，1000M 宽带理论下载约 125 MB/s，实际会受 Wi-Fi、网线、路由器和服务器影响。")

    raw = report.get("raw", {})
    stdout = raw.get("stdout", "").strip()
    stderr = raw.get("stderr", "").strip()
    if stdout or stderr:
        lines.append("")
        lines.append("原始输出")
        lines.append("-" * 34)
        if stdout:
            lines.extend(stdout.splitlines())
        if stderr:
            lines.extend(stderr.splitlines())
    return "\n".join(lines) + "\n"


def write_report_files(report: Dict[str, Any], output_dir: str) -> Tuple[Path, Path]:
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    text_path = outdir / f"cmcc_broadband_diag_{stamp}.txt"
    json_path = outdir / f"cmcc_broadband_diag_{stamp}.json"
    text_path.write_text(render_text_report(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return text_path, json_path


def write_speedtest_files(report: Dict[str, Any], output_dir: str) -> Tuple[Path, Path]:
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    text_path = outdir / f"cmcc_speedtest_{stamp}.txt"
    json_path = outdir / f"cmcc_speedtest_{stamp}.json"
    text_path.write_text(render_speedtest_report(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return text_path, json_path


def self_test() -> None:
    linux_ping = """
4 packets transmitted, 4 received, 0% packet loss, time 3004ms
rtt min/avg/max/mdev = 11.111/22.222/33.333/1.000 ms
"""
    parsed = parse_ping_output(linux_ping)
    assert parsed["packet_loss_percent"] == 0.0
    assert parsed["sent"] == 4
    assert parsed["received"] == 4
    assert parsed["avg_ms"] == 22.222

    win_ping = """
Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
Approximate round trip times in milli-seconds:
    Minimum = 12ms, Maximum = 50ms, Average = 31ms
"""
    parsed = parse_ping_output(win_ping)
    assert parsed["packet_loss_percent"] == 25.0
    assert parsed["avg_ms"] == 31.0

    synthetic = [
        CheckResult("lan", "gateway", "ok", "ok"),
        CheckResult("wan_ping", "a", "fail", "fail"),
        CheckResult("wan_ping", "b", "fail", "fail"),
        CheckResult("tcp", "a", "fail", "fail"),
    ]
    assessment = classify(synthetic, "192.168.1.1", app_group_fault=True)
    assert assessment["severity"] == "fail"
    assert "运营商" in assessment["primary"]

    sandbox = [
        CheckResult("lan", "gateway", "warn", "unknown"),
        CheckResult("wan_ping", "a", "fail", "fail"),
        CheckResult("tcp", "a", "fail", "Operation not permitted", {"error": "Operation not permitted"}),
    ]
    assessment = classify(sandbox, None, app_group_fault=True)
    assert assessment["severity"] == "warn"
    assert "运行环境" in assessment["primary"]

    nq = """
==== SUMMARY ====
Upload capacity: 57.453 Mbps
Download capacity: 713.884 Mbps
Responsiveness: High (2048 RPM)
Idle Latency: 12.333 ms
"""
    parsed_nq = parse_network_quality_output(nq)
    assert round(parsed_nq["download_mbps"], 3) == 713.884
    assert round(parsed_nq["upload_mbps"], 3) == 57.453
    assert parsed_nq["responsiveness_rpm"] == 2048

    nq_new = """
==== SUMMARY ====
Uplink capacity: 105.894 Mbps
Downlink capacity: 50.408 Mbps
Uplink Responsiveness: Low (334.365 milliseconds | 179 RPM)
Downlink Responsiveness: Low (436.658 milliseconds | 137 RPM)
Idle Latency: 162.095 milliseconds | 370 RPM
"""
    parsed_nq_new = parse_network_quality_output(nq_new)
    assert round(parsed_nq_new["download_mbps"], 3) == 50.408
    assert round(parsed_nq_new["upload_mbps"], 3) == 105.894
    assert round(parsed_nq_new["idle_latency_ms"], 3) == 162.095
    assert parsed_nq_new["download_responsiveness_rpm"] == 137
    assert parsed_nq_new["upload_responsiveness_rpm"] == 179

    nq_tls_partial = """
==== SUMMARY ====
Downlink capacity: 10.670 Mbps
Downlink Responsiveness: Low (3.540 seconds | 16 RPM)
Idle Latency: 314.403 milliseconds | 190 RPM
Error: Error Domain=NSURLErrorDomain Code=-1200 "A TLS error caused the secure connection to fail."
"""
    parsed_nq_tls = parse_network_quality_output(nq_tls_partial)
    assert round(parsed_nq_tls["download_mbps"], 3) == 10.67
    assert parsed_nq_tls["download_responsiveness_rpm"] == 16
    assert "upload_mbps" not in parsed_nq_tls
    assert "TLS 错误" in network_quality_notes({"ok": False, "stdout": nq_tls_partial, "stderr": ""}, parsed_nq_tls)[0]

    nq_zero_rpm = "Downlink Responsiveness: Low (inf seconds | 0 RPM)"
    parsed_nq_zero = parse_network_quality_output(nq_zero_rpm)
    assert parsed_nq_zero["download_responsiveness_rpm"] == 0

    ookla = """
{"type":"result","ping":{"jitter":0.583,"latency":5.778},"download":{"bandwidth":92345678},"upload":{"bandwidth":12345678},"server":{"name":"China Mobile","location":"Shanghai","country":"China"},"result":{"url":"https://www.speedtest.net/result/c/abc"}}
"""
    parsed_ookla = parse_ookla_speedtest_json(ookla)
    assert round(parsed_ookla["download_mbps"], 3) == 738.765
    assert round(parsed_ookla["upload_mbps"], 3) == 98.765
    assert round(parsed_ookla["idle_latency_ms"], 3) == 5.778
    assert round(parsed_ookla["jitter_ms"], 3) == 0.583
    assert "China Mobile" in parsed_ookla["server"]
    assert parsed_ookla["result_url"].startswith("https://")

    cli = """
{"download":502000000.0,"upload":84200000.0,"ping":13.25,"server":{"sponsor":"CMCC","name":"Shanghai","country":"China"}}
"""
    parsed_cli = parse_speedtest_cli_json(cli)
    assert round(parsed_cli["download_mbps"], 3) == 502.0
    assert round(parsed_cli["upload_mbps"], 3) == 84.2
    assert round(parsed_cli["idle_latency_ms"], 3) == 13.25
    assert "CMCC" in parsed_cli["server"]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="家庭宽带故障诊断：区分本地 Wi-Fi/路由器、DNS、网页访问和运营商线路侧问题。"
    )
    parser.add_argument(
        "--app-group-fault",
        action="store_true",
        help="中国移动 App/智修精灵已显示“存在群障/线路状态异常”时加上此参数。",
    )
    parser.add_argument("--deep", action="store_true", help="增加 traceroute 路由跟踪，耗时更久。")
    parser.add_argument("--count", type=int, default=4, help="每个目标 ping 次数，默认 4。")
    parser.add_argument("--timeout-ms", type=int, default=1500, help="单次网络检测超时，默认 1500 ms。")
    parser.add_argument("--output-dir", default="reports", help="报告输出目录，默认 reports。")
    parser.add_argument("--no-write", action="store_true", help="只在屏幕显示报告，不写入文件。")
    parser.add_argument("--json", action="store_true", help="在屏幕输出 JSON，而不是文本报告。")
    parser.add_argument("--speedtest", action="store_true", help="测下载和上载速度；macOS 用 networkQuality，Linux 用 speedtest/speedtest-cli。")
    parser.add_argument("--speedtest-seconds", type=int, default=60, help="测速最大运行秒数，默认 60。")
    parser.add_argument("--self-test", action="store_true", help="运行脚本内部自检，不访问网络。")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        self_test()
        print("self-test ok")
        return 0

    if args.speedtest:
        report = run_speedtest(args.speedtest_seconds)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_speedtest_report(report))
        if not args.no_write:
            text_path, json_path = write_speedtest_files(report, args.output_dir)
            print(f"测速报告已保存: {text_path}")
            print(f"原始 JSON: {json_path}")
        return 0

    report = collect_diagnostics(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text_report(report))

    if not args.no_write:
        text_path, json_path = write_report_files(report, args.output_dir)
        print(f"报告已保存: {text_path}")
        print(f"原始 JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
