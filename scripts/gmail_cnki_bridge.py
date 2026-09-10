#!/usr/bin/env python3
"""
Gmail ↔ CNKI Literature Bridge (Nature-Skills Edition)
======================================================
把「只推知网文献到 Gmail → 下载知网 PDF 回传 Gmail → 攒够阈值自动生成综述」
的完整闭环封装为一个脚本，四个子命令：

  selftest   离线全链路仿真（无需网络、无需凭据）：
             任务单邮件构建 → 知网过滤 → PDF 附件回环 → 批次阈值触发综述
  push       发送「仅知网」文献任务单邮件到 Gmail（SMTP）
  ingest     对任务单里每条知网题名：
                a) 调 nature-downloader（batch_download.mjs，CNKI 路由）下载真实 PDF；或
                b) 直接打包本地目录里已有的 PDF（--local-pdf-dir，跳过下载器）
             然后作为 [CNKI-INGEST] PDF 附件邮件回传 Gmail
  watch      IMAP 轮询 Gmail，接收 [CNKI-INGEST] 邮件 →
             PDF 归档到本地文献库 → LiteratureAccumulator 累积 →
             达到批次阈值自动触发 ARTA 综述编译

凭据（环境变量或参数，切勿硬编码进代码/聊天）：
  GMAIL_EMAIL         你的 Gmail 账号
  GMAIL_APP_PASSWORD  16 位应用专用密码（需先在谷歌账号开启两步验证）

典型用法（先配置 GMAIL_EMAIL / GMAIL_APP_PASSWORD 环境变量）：
  # 0) 任意环境先跑全链路自检
  python3 scripts/gmail_cnki_bridge.py --selftest

  # 1) 发送知网任务单（cnki_papers.json 为条目列表或 manifest 字典）
  python3 scripts/gmail_cnki_bridge.py push --entries outputs/cnki_papers.json

  # 2) 下载真实知网 PDF 并回传（需已登录 CNKI 的 Chrome + node 22+）
  python3 scripts/gmail_cnki_bridge.py ingest --entries outputs/cnki_papers.json

  # 3) 或：直接把本地已保存的 PDF 打包回传（跳过下载器）
  python3 scripts/gmail_cnki_bridge.py ingest --entries outputs/cnki_papers.json \
      --local-pdf-dir outputs/cnki_downloads

  # 4) 接收 + 累积 + 触发综述（一次性或守护）
  python3 scripts/gmail_cnki_bridge.py watch --batch-size 10 --topic "鲜味肽机器学习筛选" --daemon

边界与合规：
  - 真实知网下载依赖你机构授权的登录态（CNKI 无公开 API），只处理明确任务单，不做批量扫库。
  - 仿真产物（cnki_pdf_downloader 内置目录）仅用于流程验证，不是真实文献。
"""

from __future__ import annotations

import argparse
import dataclasses
import email
import hashlib
import imaplib
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from dataclasses import dataclass, field
from email.message import EmailMessage, Message
from email.header import decode_header, make_header
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from gmail_literature_trigger import LiteratureAccumulator, QueuedPaper  # noqa: E402
from cnki_pdf_downloader import CNKIPDFDownloader  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

DIGEST_PREFIX = "[CNKI-DIGEST]"
INGEST_PREFIX = "[CNKI-INGEST]"

JSON_MARKER_OPEN = "<<<CNKI_JSON>>>"
JSON_MARKER_CLOSE = "<<<END_CNKI_JSON>>>"

SUCCESS_DOWNLOAD_STATUSES = {
    "downloaded",
    "downloaded_with_si",
    "native_fulltext_downloaded",
    "open_access_downloaded",
}

CJK_RE = re.compile(r"[\u3400-\u9fff]")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class GmailConfig:
    email: str
    app_password: str
    smtp_host: str = SMTP_HOST
    smtp_port: int = SMTP_PORT
    imap_host: str = IMAP_HOST
    imap_port: int = IMAP_PORT

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "GmailConfig":
        email = getattr(args, "email", None) or os.environ.get("GMAIL_EMAIL", "")
        password = getattr(args, "app_password", None) or os.environ.get("GMAIL_APP_PASSWORD", "")
        return cls(email=email, app_password=password)

    def require(self) -> None:
        if not self.email or not self.app_password:
            raise SystemExit(
                "缺少 Gmail 凭据：请设置环境变量 GMAIL_EMAIL / GMAIL_APP_PASSWORD "
                "或传 --email / --app-password（应用专用密码，非登录密码）。"
            )


# ---------------------------------------------------------------------------
# 条目规范化与知网过滤
# ---------------------------------------------------------------------------

def default_item_key(title: str) -> str:
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()[:6].upper()
    return f"CNKI_{digest}"


def safe_cjk_slug(title: str, limit: int = 40) -> str:
    cleaned = re.sub(r'[\/:*?"<>|]+', "_", str(title).strip())
    return cleaned[:limit]


def normalize_entries(raw: Any) -> List[Dict[str, Any]]:
    """把列表 / manifest 字典统一规范化为条目字典列表。"""
    if isinstance(raw, dict):
        raw = raw.get("papers") or raw.get("entries") or raw.get("results") or []
    if not isinstance(raw, list):
        return []
    entries: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        authors = item.get("authors") or []
        if isinstance(authors, str):
            authors = [a.strip() for a in re.split(r"[;,，；\s]+", authors) if a.strip()]
        entry = {
            "item_key": str(item.get("item_key") or "").strip() or default_item_key(title),
            "title": title,
            "authors": [str(a) for a in authors],
            "year": str(item.get("year") or ""),
            "journal": str(item.get("journal") or ""),
            "doi": str(item.get("doi") or ""),
            "cnki_url": str(item.get("cnki_url") or item.get("url") or ""),
            "abstract": str(item.get("abstract") or ""),
            "source": str(item.get("source") or "cnki"),
        }
        entries.append(entry)
    return entries


def _is_cnki_host(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = url.lower()
    return (
        host == "cnki.net" or host.endswith(".cnki.net")
        or host == "cnki.com.cn" or host.endswith(".cnki.com.cn")
    )


def is_cnki_entry(entry: Dict[str, Any]) -> bool:
    """「只推知网」过滤：来源标注 / 中文题名 / CNKI 链接，三者任一命中。"""
    source = str(entry.get("source", "")).strip().lower()
    if source in {"cnki", "zhiwang", "cnki 知网", "china national knowledge infrastructure"}:
        return True
    if CJK_RE.search(entry.get("title", "")):
        return True
    url = entry.get("cnki_url", "")
    if url and _is_cnki_host(url):
        return True
    return False


def load_entries(path: str) -> List[Dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_entries(data)


# ---------------------------------------------------------------------------
# 邮件构建与解析
# ---------------------------------------------------------------------------

def _json_block(papers: List[Dict[str, Any]]) -> str:
    payload = json.dumps({"papers": papers}, ensure_ascii=False, separators=(",", ":"))
    return f"{JSON_MARKER_OPEN} {payload} {JSON_MARKER_CLOSE}"


def build_digest_message(
    entries: List[Dict[str, Any]],
    cfg: GmailConfig,
    topic: str,
    to: Optional[str] = None,
) -> EmailMessage:
    """构建 [CNKI-DIGEST] 任务单邮件：人类可读摘要 + 机器可读 JSON（正文标记 + 附件）。"""
    today = time.strftime("%Y-%m-%d")
    lines: List[str] = []
    lines.append(f"{DIGEST_PREFIX} 知网文献任务单")
    lines.append(f"主题: {topic}")
    lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"条目数: {len(entries)}（仅知网中文文献，英文条目已过滤）")
    lines.append("")
    for idx, e in enumerate(entries, 1):
        lines.append(f"#{idx} {e['title']}")
        lines.append(f"   作者: {'; '.join(e['authors']) if e['authors'] else '—'}")
        lines.append(f"   期刊: {e['journal']} | 年份: {e['year']} | DOI: {e['doi'] or '—'}")
        lines.append(f"   链接: {e['cnki_url'] or '—'}")
        lines.append("")
    lines.append("[下一步] 下载端执行：")
    lines.append("  python3 scripts/gmail_cnki_bridge.py ingest --entries <本邮件附件 cnki_papers.json>")
    lines.append("")
    lines.append(_json_block(entries))

    msg = EmailMessage()
    msg["From"] = cfg.email
    msg["To"] = to or cfg.email
    msg["Subject"] = f"{DIGEST_PREFIX} {topic} | {today} | {len(entries)} 篇"
    msg.set_content("\n".join(lines))
    msg.add_attachment(
        json.dumps({"papers": entries}, ensure_ascii=False, indent=2).encode("utf-8"),
        maintype="application",
        subtype="json",
        filename="cnki_papers.json",
    )
    return msg


def _body_text(msg: Message) -> str:
    try:
        content = msg.get_content()
        if isinstance(content, str):
            return content
    except Exception:
        pass
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True)
        if payload is not None:
            return payload.decode("utf-8", "replace")
        return str(msg.get_payload() or "")
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload is not None:
                return payload.decode("utf-8", "replace")
    return ""


def parse_cnki_entries_from_message(msg: Message) -> List[Dict[str, Any]]:
    """从任务单/回传邮件中恢复条目：优先 JSON 附件，其次正文标记块。"""
    for part in msg.iter_parts():
        filename = part.get_filename() or ""
        if filename.endswith(".json") and part.get_content_type() == "application/json":
            try:
                data = json.loads(part.get_payload(decode=True).decode("utf-8"))
                return normalize_entries(data)
            except Exception:
                continue
    body = _body_text(msg)
    match = re.search(re.escape(JSON_MARKER_OPEN) + r"(.*?)" + re.escape(JSON_MARKER_CLOSE), body, re.S)
    if match:
        try:
            return normalize_entries(json.loads(match.group(1)))
        except Exception:
            pass
    return []


def build_ingest_message(
    entry: Dict[str, Any],
    pdf_bytes: bytes,
    cfg: GmailConfig,
    sha256: Optional[str] = None,
    to: Optional[str] = None,
) -> EmailMessage:
    """构建 [CNKI-INGEST] PDF 回传邮件（附件文件名为 ASCII，中文题名在元数据 JSON 中）。"""
    meta = dict(entry)
    meta["pdf_sha256"] = sha256 or hashlib.sha256(pdf_bytes).hexdigest()
    subject = f"{INGEST_PREFIX} {entry['item_key']} | {entry['title'][:48]}"

    body = (
        f"本邮件由 CNKI-Gmail 桥自动发送，请勿回复。\n"
        f"题名: {entry['title']}\n"
        f"期刊: {entry['journal']} | 年份: {entry['year']} | DOI: {entry['doi'] or '—'}\n"
        f"附件: {meta['pdf_sha256'][:16]}… ({len(pdf_bytes)} bytes)\n\n"
        f"{_json_block([entry])}\n"
    )
    msg = EmailMessage()
    msg["From"] = cfg.email
    msg["To"] = to or cfg.email
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                       filename=f"{entry['item_key']}_paper.pdf")
    return msg


# ---------------------------------------------------------------------------
# SMTP 发送
# ---------------------------------------------------------------------------

def send_message(cfg: GmailConfig, msg: EmailMessage) -> None:
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as server:
        server.starttls()
        server.login(cfg.email, cfg.app_password)
        server.send_message(msg)


def save_eml(msg: EmailMessage, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msg.as_bytes())
    return path


# ---------------------------------------------------------------------------
# 下载（nature-downloader CNKI 路由 / 本地目录）
# ---------------------------------------------------------------------------

def default_skill_dir() -> Path:
    return REPO_ROOT / "skills" / "nature-downloader"


def download_with_nature_downloader(
    entry: Dict[str, Any],
    work_dir: Path,
    node: str = "node",
    skill_dir: Optional[Path] = None,
    timeout: int = 900,
) -> Tuple[Optional[Path], Optional[str]]:
    """调 batch_download.mjs（中文题名 → CNKI 路由），返回 (pdf 路径, 失败原因)。"""
    skill_dir = skill_dir or default_skill_dir()
    script = skill_dir / "scripts" / "batch_download.mjs"
    if not script.exists():
        return None, f"未找到 nature-downloader 脚本: {script}"
    out_dir = work_dir / entry["item_key"]
    cmd = [
        node, str(script),
        "--title", entry["title"],
        "--cnki-format", "pdf",
        "--no-si",
        "--out", str(out_dir),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "下载器超时（机构登录可能失效，请检查 Chrome 登录态）"
    except FileNotFoundError:
        return None, f"未找到 node 可执行文件: {node}"
    stdout = proc.stdout.strip()
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or stdout)[-300:]
        return None, f"无法解析下载器输出。stderr 尾部: {tail}"
    for result in data.get("results", []):
        status = result.get("status", "")
        file_path = result.get("file")
        if status in SUCCESS_DOWNLOAD_STATUSES and file_path and Path(file_path).exists():
            return Path(file_path), None
    statuses = [r.get("status") for r in data.get("results", [])]
    next_actions = [r.get("next_action", "") for r in data.get("results", []) if r.get("next_action")]
    detail = "; ".join(next_actions[:1])
    return None, f"下载失败: {statuses} {detail}".strip()


def find_local_pdf(local_dir: Path, entry: Dict[str, Any]) -> Optional[Path]:
    """在本地目录里按 item_key 前缀 / 题名前缀匹配 PDF 或 CAJ。"""
    candidates = [p for p in local_dir.iterdir() if p.suffix.lower() in {".pdf", ".caj"} and p.is_file()]
    key_match = [p for p in candidates if p.name.startswith(entry["item_key"])]
    if key_match:
        return sorted(key_match, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    title_prefix = safe_cjk_slug(entry["title"], limit=12)
    title_match = [p for p in candidates if title_prefix and title_prefix in p.name]
    if title_match:
        return sorted(title_match, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return None


def caj_to_pdf(caj_path: Path, out_dir: Path) -> Optional[Path]:
    """best-effort：调用仓库内 caj_to_pdf_converter.py 转 PDF。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = out_dir / f"{caj_path.stem}.pdf"
    try:
        subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "caj_to_pdf_converter.py"),
             "--caj-file", str(caj_path), "--out-dir", str(out_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except Exception:
        return None
    return expected if expected.exists() else None


# ---------------------------------------------------------------------------
# 接收侧：处理 [CNKI-INGEST] 邮件 → 归档 → 累积 → 触发综述
# ---------------------------------------------------------------------------

def _load_json_file(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def process_ingest_message(
    msg: Message,
    library_dir: Path,
    accumulator: LiteratureAccumulator,
    topic: str,
) -> Dict[str, Any]:
    """处理一封 [CNKI-INGEST] 邮件：存 PDF、去重、入累积队列，达到阈值触发综述。"""
    library_dir.mkdir(parents=True, exist_ok=True)
    entries = parse_cnki_entries_from_message(msg)
    if not entries:
        return {"status": "skipped", "reason": "no_cnki_entries_found"}
    entry = entries[0]

    # 提取附件
    pdf_bytes: Optional[bytes] = None
    attachment_name = ""
    for part in msg.iter_parts():
        filename = part.get_filename() or ""
        content_type = part.get_content_type()
        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            pdf_bytes = part.get_payload(decode=True)
            attachment_name = filename
            break
    if pdf_bytes is None:
        # CAJ 附件 → best-effort 转 PDF
        for part in msg.iter_parts():
            filename = part.get_filename() or ""
            if filename.lower().endswith(".caj"):
                caj_tmp = library_dir / "incoming" / f"{entry['item_key']}.caj"
                caj_tmp.parent.mkdir(parents=True, exist_ok=True)
                caj_tmp.write_bytes(part.get_payload(decode=True))
                converted = caj_to_pdf(caj_tmp, library_dir / "incoming")
                if converted:
                    pdf_bytes = converted.read_bytes()
                    attachment_name = converted.name
                break
    if pdf_bytes is None:
        return {"status": "skipped", "reason": "no_pdf_attachment", "item_key": entry["item_key"]}

    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    index = _load_json_file(library_dir / "index.json", {})
    if sha256 in index:
        return {"status": "duplicate", "item_key": entry["item_key"], "sha256": sha256}

    paper_dir = library_dir / entry["item_key"]
    paper_dir.mkdir(parents=True, exist_ok=True)
    saved_path = paper_dir / f"{entry['item_key']}.pdf"
    saved_path.write_bytes(pdf_bytes)
    index[sha256] = {
        "item_key": entry["item_key"],
        "title": entry["title"],
        "saved_path": str(saved_path),
        "received_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_json_file(library_dir / "index.json", index)

    queued = QueuedPaper(
        item_key=entry["item_key"],
        title=entry["title"],
        authors=entry["authors"],
        year=entry["year"],
        journal=entry["journal"],
        doi=entry["doi"],
        abstract=entry["abstract"],
        received_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        pdf_path=str(saved_path),
        source="CNKI-Gmail",
    )
    result = accumulator.add_paper(queued, topic=topic, output_dir=str(library_dir / "reviews"))
    return {
        "status": "ingested",
        "item_key": entry["item_key"],
        "saved_path": str(saved_path),
        "sha256": sha256,
        "queue_count": len(accumulator.queue),
        "review_triggered": bool(result),
        "review_result": result,
    }


def decode_mime_header(raw: str) -> str:
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


def watch_once(
    cfg: GmailConfig,
    accumulator: LiteratureAccumulator,
    library_dir: Path,
    topic: str,
    folder: str = "INBOX",
    mark_seen: bool = False,
) -> List[Dict[str, Any]]:
    """单次 IMAP 轮询：处理所有未读 [CNKI-INGEST] 邮件，返回处理报告列表。"""
    processed = _load_json_file(library_dir / "processed_messages.json", {})
    reports: List[Dict[str, Any]] = []
    with imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port) as conn:
        conn.login(cfg.email, cfg.app_password)
        typ, data = conn.select(folder, readonly=False)
        if typ != "OK":
            raise SystemExit(f"IMAP 选择文件夹失败: {folder}")
        typ, data = conn.search(None, "UNSEEN")
        uids = (data[0] or b"").split()
        for uid in uids:
            typ, hdr_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID)])")
            if typ != "OK" or not hdr_data or hdr_data[0] is None:
                continue
            header_msg = email.message_from_bytes(hdr_data[0][1])
            subject = decode_mime_header(header_msg.get("Subject", ""))
            if not subject.startswith(INGEST_PREFIX):
                continue
            message_id = (header_msg.get("Message-ID") or "").strip() or f"uid:{uid.decode()}"
            if message_id in processed:
                continue
            typ, mdata = conn.fetch(uid, "(RFC822)")
            if typ != "OK" or not mdata or mdata[0] is None:
                continue
            full_msg = email.message_from_bytes(mdata[0][1])
            report = process_ingest_message(full_msg, library_dir, accumulator, topic)
            report["message_id"] = message_id
            processed[message_id] = {"processed_at": time.strftime("%Y-%m-%d %H:%M:%S"), **report}
            _save_json_file(library_dir / "processed_messages.json", processed)
            if mark_seen:
                conn.store(uid, "+FLAGS", "\\Seen")
            reports.append(report)
    return reports


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_push(args: argparse.Namespace, cfg: GmailConfig) -> None:
    entries = load_entries(args.entries)
    cnki_only = [e for e in entries if is_cnki_entry(e)]
    dropped = len(entries) - len(cnki_only)
    print(f"📤 [push] 共 {len(entries)} 条，知网过滤后 {len(cnki_only)} 条（过滤掉 {dropped} 条非知网条目）")
    if not cnki_only:
        print("⚠️  没有可推送的知网条目，终止。")
        return
    msg = build_digest_message(cnki_only, cfg, topic=args.topic, to=args.to)
    if args.dry_run:
        out = save_eml(msg, Path("outputs") / "gmail_bridge_dryrun" / f"digest_{int(time.time())}.eml")
        print(f"✅ [dry-run] 未发送，任务单邮件已保存: {out}")
    else:
        cfg.require()
        send_message(cfg, msg)
        print(f"✅ [push] 任务单已发送至 {args.to or cfg.email}（主题: {msg['Subject']}）")
        print("   下一步: python3 scripts/gmail_cnki_bridge.py ingest --entries <同一份条目 JSON>")


def cmd_ingest(args: argparse.Namespace, cfg: GmailConfig) -> None:
    entries = [e for e in load_entries(args.entries) if is_cnki_entry(e)]
    if not entries:
        print("⚠️  没有可回传的知网条目，终止。")
        return
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    report_dir = work_dir / "ingest_report"
    report: Dict[str, Any] = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "entries": []}

    print(f"📥 [ingest] 共 {len(entries)} 条知网条目待回传")
    for idx, entry in enumerate(entries, 1):
        print(f"[{idx}/{len(entries)}] {entry['item_key']} 《{entry['title']}》")
        pdf_path: Optional[Path] = None
        error: Optional[str] = None

        if args.local_pdf_dir:
            local_dir = Path(args.local_pdf_dir).expanduser()
            found = find_local_pdf(local_dir, entry) if local_dir.exists() else None
            if found:
                pdf_path = found
            else:
                error = f"本地目录未找到对应 PDF/CAJ: {local_dir}"
        else:
            pdf_path, error = download_with_nature_downloader(
                entry, work_dir, node=args.node,
                skill_dir=Path(args.skill_dir) if args.skill_dir else None,
            )
            if error is None and pdf_path and pdf_path.suffix.lower() == ".caj":
                converted = caj_to_pdf(pdf_path, work_dir / "caj_converted")
                if converted:
                    pdf_path = converted
                else:
                    error = "CAJ 转换失败（需 PyMuPDF：pip install pymupdf）"

        item_report: Dict[str, Any] = {"item_key": entry["item_key"], "title": entry["title"]}
        if error or pdf_path is None:
            item_report["status"] = "failed"
            item_report["reason"] = error or "no_pdf"
            print(f"   ❌ 失败: {item_report['reason']}")
        else:
            pdf_bytes = pdf_path.read_bytes()
            msg = build_ingest_message(entry, pdf_bytes, cfg)
            if args.dry_run:
                saved = save_eml(msg, report_dir / f"{entry['item_key']}_ingest.eml")
                item_report["status"] = "dry_run_saved"
                item_report["eml"] = str(saved)
                print(f"   ✅ [dry-run] 回传邮件已保存: {saved}")
            else:
                cfg.require()
                try:
                    send_message(cfg, msg)
                    item_report["status"] = "sent"
                    print(f"   ✅ 已回传 Gmail（附件 {len(pdf_bytes) / 1024:.1f} KB）")
                except Exception as exc:
                    item_report["status"] = "send_failed"
                    item_report["reason"] = str(exc)
                    print(f"   ❌ 发送失败: {exc}")
        item_report["pdf_path"] = str(pdf_path) if pdf_path else None
        report["entries"].append(item_report)
        time.sleep(args.pace)

    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report_path = report_dir / "ingest_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for e in report["entries"] if e["status"] in {"sent", "dry_run_saved"})
    print(f"🎉 [ingest] 完成: {ok}/{len(entries)} 成功。报告: {report_path}")


def cmd_watch(args: argparse.Namespace, cfg: GmailConfig) -> None:
    cfg.require()
    library_dir = Path(args.library)
    accumulator = LiteratureAccumulator(
        state_file=str(library_dir / "accumulator_state.json"), batch_size=args.batch_size
    )
    print(f"📡 [watch] 监听 Gmail（{cfg.email} / {args.folder}） | 主题: {args.topic}")
    print(f"   文献库: {library_dir.resolve()} | 批次阈值: {args.batch_size} 篇")

    def run_once() -> None:
        reports = watch_once(
            cfg, accumulator, library_dir, topic=args.topic,
            folder=args.folder, mark_seen=args.mark_seen,
        )
        if not reports:
            print(f"   … 本轮无新 {INGEST_PREFIX} 邮件（队列 {len(accumulator.queue)}/{args.batch_size}）")
            return
        for r in reports:
            if r["status"] == "ingested":
                print(f"   📥 入库: {r['item_key']}（队列 {r['queue_count']}/{args.batch_size}）")
                if r.get("review_triggered"):
                    res = r["review_result"]
                    print(f"   🎉 批次 #{res['batch_id']} 综述编译完成:")
                    print(f"      📄 {res['chapter1_path']}")
                    print(f"      📦 {res['payload_path']}")
                    print(f"      📊 {res['ppt_path']}")
            else:
                print(f"   ⏭️  跳过: {r.get('item_key', '?')} ({r['status']})")

    if args.daemon:
        try:
            while True:
                try:
                    run_once()
                except Exception as exc:  # 守护模式下网络抖动不应终止
                    print(f"   ⚠️  轮询异常（{args.interval}s 后重试）: {exc}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n👋 [watch] 已停止。")
    else:
        run_once()


def cmd_selftest(args: argparse.Namespace) -> None:
    """离线全链路仿真：不触网、不需要凭据。"""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    topic = "食源性鲜味肽高通量筛选与呈味机制解析"
    cfg = GmailConfig(email="selftest@example.org", app_password="")
    failures: List[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    print("=" * 72)
    print("🧪 [selftest] Gmail ↔ CNKI 桥离线全链路仿真")
    print("=" * 72)

    # A. 样本条目（4 篇知网 + 1 篇英文，验证过滤）
    downloader = CNKIPDFDownloader(output_dir=str(out / "downloads"))
    catalog = downloader._query_cnki_metadata(topic, 4)
    entries = [
        {
            "item_key": it["item_key"],
            "title": it["title"],
            "authors": it["authors"],
            "year": it["year"],
            "journal": it["journal"],
            "doi": it["doi"],
            "cnki_url": it["cnki_url"],
            "abstract": it["abstract"],
            "source": "cnki",
        }
        for it in catalog
    ]
    entries.append({
        "item_key": "ENG_999",
        "title": "Deep Learning for Protein Structure Prediction",
        "authors": ["Smith J."],
        "year": "2025",
        "journal": "Nature Methods",
        "doi": "10.1038/s41592-025-00001-x",
        "cnki_url": "",
        "abstract": "English paper, must be filtered out by CNKI-only rule.",
        "source": "sciencedirect",
    })
    cnki_only = [e for e in entries if is_cnki_entry(e)]
    check("知网过滤（4 中 1 英 → 仅 4 条中文）", len(cnki_only) == 4, f"保留 {len(cnki_only)}/5")

    # B. 任务单邮件回环
    digest = build_digest_message(cnki_only, cfg, topic=topic)
    parsed = parse_cnki_entries_from_message(digest)
    titles_ok = all(p["title"] == e["title"] for p, e in zip(parsed, cnki_only))
    check("任务单邮件 JSON 回环（附件 + 正文标记）", len(parsed) == 4 and titles_ok)

    # C. 生成样本 PDF → 回传邮件 → 接收侧处理 → 批次触发综述
    library = out / "library"
    accumulator = LiteratureAccumulator(state_file=str(library / "accumulator_state.json"), batch_size=len(cnki_only))
    catalog_by_title = {it["title"]: it for it in catalog}
    for entry in cnki_only:
        it = catalog_by_title[entry["title"]]
        pdf_target = out / "downloads" / f"{it['item_key']}.pdf"
        downloader._download_or_build_pdf(it, pdf_target)
        ingest_msg = build_ingest_message(entry, pdf_target.read_bytes(), cfg)
        result = process_ingest_message(ingest_msg, library, accumulator, topic)
        check(f"入库 {entry['item_key']} 《{entry['title'][:24]}…》",
              result["status"] == "ingested" and Path(result["saved_path"]).exists())

    check("批次阈值触发综述编译", accumulator.processed_batches == 1)
    batch_dir = library / "reviews" / "batch_1"
    for artifact in ("thesis_chapter1_review.md", "arta_synthesis_payload.json", "arta_ppt_payload.json"):
        check(f"综述产物 {artifact}", (batch_dir / artifact).exists())

    # D. 去重：同一 PDF 再次回传应被识别
    ingest_again = build_ingest_message(cnki_only[0], (out / "downloads" / f"{cnki_only[0]['item_key']}.pdf").read_bytes(), cfg)
    dup_result = process_ingest_message(ingest_again, library, accumulator, topic)
    check("SHA-256 去重", dup_result["status"] == "duplicate")

    print("=" * 72)
    if failures:
        print(f"❌ SELF-TEST FAILED: {len(failures)} 项未通过: {failures}")
        sys.exit(1)
    print("✅ SELF-TEST PASS — 全链路（过滤/邮件回环/入库/批次触发/去重）验证通过")
    print(f"   仿真产物目录: {out.resolve()}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Gmail ↔ CNKI 文献桥（Nature-Skills）")
    parser.add_argument("--selftest", action="store_true", help="离线全链路仿真（默认）")
    parser.add_argument("--out", type=str, default="outputs/gmail_bridge_selftest", help="selftest 输出目录")
    parser.add_argument("--email", type=str, default=None, help="Gmail 账号（或 env GMAIL_EMAIL）")
    parser.add_argument("--app-password", dest="app_password", type=str, default=None,
                        help="Gmail 应用专用密码（或 env GMAIL_APP_PASSWORD）")
    sub = parser.add_subparsers(dest="mode")

    p_push = sub.add_parser("push", help="发送仅知网的文献任务单邮件")
    p_push.add_argument("--entries", required=True, help="条目 JSON（列表或 manifest 字典）")
    p_push.add_argument("--topic", type=str, default="知网文献监测")
    p_push.add_argument("--to", type=str, default=None, help="收件人（默认发给自己）")
    p_push.add_argument("--dry-run", action="store_true", help="不发送，保存 .eml")

    p_ingest = sub.add_parser("ingest", help="下载/打包知网 PDF 并回传 Gmail")
    p_ingest.add_argument("--entries", required=True)
    p_ingest.add_argument("--local-pdf-dir", type=str, default=None,
                          help="跳过下载器，直接打包该目录里已保存的 PDF/CAJ")
    p_ingest.add_argument("--work-dir", type=str, default="outputs/cnki_ingest")
    p_ingest.add_argument("--node", type=str, default="node")
    p_ingest.add_argument("--skill-dir", type=str, default=None,
                          help="nature-downloader 目录（默认仓库内 skills/nature-downloader）")
    p_ingest.add_argument("--to", type=str, default=None)
    p_ingest.add_argument("--pace", type=float, default=1.0, help="每条之间的间隔秒数")
    p_ingest.add_argument("--dry-run", action="store_true", help="不发送，保存 .eml")

    p_watch = sub.add_parser("watch", help="IMAP 接收 [CNKI-INGEST] 邮件并累积触发综述")
    p_watch.add_argument("--folder", type=str, default="INBOX")
    p_watch.add_argument("--batch-size", type=int, default=10)
    p_watch.add_argument("--topic", type=str, default="知网文献自动综述")
    p_watch.add_argument("--library", type=str, default="outputs/gmail_library")
    p_watch.add_argument("--daemon", action="store_true", help="持续轮询（Ctrl+C 退出）")
    p_watch.add_argument("--interval", type=int, default=300, help="轮询间隔秒")
    p_watch.add_argument("--mark-seen", action="store_true", help="处理完标记 Gmail 已读")

    args = parser.parse_args()
    cfg = GmailConfig.from_args(args)

    if args.selftest or args.mode is None:
        cmd_selftest(args)
    elif args.mode == "push":
        cmd_push(args, cfg)
    elif args.mode == "ingest":
        cmd_ingest(args, cfg)
    elif args.mode == "watch":
        cmd_watch(args, cfg)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
