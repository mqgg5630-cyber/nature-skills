#!/usr/bin/env python3
"""
Spark 事件钩子（Gmail ↔ CNKI 桥的下游触发器）
=============================================
被 `gmail_cnki_bridge.py watch --on-event` 调用：邮件一到 / 综述一出，
本脚本立刻拿到事件 JSON，并把它转成 Spark（你自己的编排 Agent）能消费的形式。

事件从两个渠道同时送达（任选其一读取）：
  - stdin：完整事件 JSON
  - 环境变量：CNKI_EVENT（完整 JSON）、CNKI_EVENT_TYPE、CNKI_ITEM_KEY、CNKI_PDF_PATH

事件类型：
  paper_ingested  单篇知网 PDF 已入库（含 item_key / title / pdf_path / queue_count）
  review_ready    达到批次阈值，综述已编译（含 chapter1_path / payload_path / ppt_path）

默认行为（不依赖任何第三方库）：
  1. 追加写入 JSONL 事件日志，Spark 可以 tail 这个文件做长轮询；
  2. 为每个事件写一份 `tasks/<时间戳>_<事件类型>.json` 任务单（Spark 扫目录即可取任务）；
  3. review_ready 时额外打印醒目提示，并可选调用你指定的命令（--spark-cmd）。

用法（挂到 watch 上）：
  python3 scripts/gmail_cnki_bridge.py watch --daemon --push-mode idle \
      --batch-size 10 --topic "食源性鲜味肽" \
      --on-event "python3 scripts/spark_event_hook.py"

  # 想让 Spark 命令行直接接管：
  --on-event "python3 scripts/spark_event_hook.py --spark-cmd 'spark run review --payload {payload_path}'"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict


def read_event() -> Dict[str, Any]:
    """优先读 stdin，退回环境变量 CNKI_EVENT。"""
    raw = ""
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read().strip()
        except Exception:
            raw = ""
    if not raw:
        raw = os.environ.get("CNKI_EVENT", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"event": "unparsable", "raw": raw[:500]}


def append_jsonl(path: Path, event: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def write_task(tasks_dir: Path, event: Dict[str, Any]) -> Path:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    kind = str(event.get("event", "event"))
    key = str(event.get("item_key") or event.get("batch_id") or "na")
    path = tasks_dir / f"{stamp}_{kind}_{key}.json"
    task = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending",
        "event": event,
        "suggested_action": SUGGESTED_ACTIONS.get(kind, "人工确认"),
    }
    path.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


SUGGESTED_ACTIONS = {
    "paper_ingested": "对该 PDF 做结构化抽取（PaperCard），核对数据可溯源到具体页码",
    "review_ready": "对综述底本做五支柱审计：定量溯源≥90%、因果链、局限性、非流水账论证、零虚假引用",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Spark 事件钩子：把 Gmail↔CNKI 事件转成 Spark 任务")
    parser.add_argument("--log", type=str, default="outputs/spark_events/events.jsonl",
                        help="JSONL 事件日志路径（Spark 可 tail）")
    parser.add_argument("--tasks-dir", type=str, default="outputs/spark_events/tasks",
                        help="任务单目录（Spark 可扫目录取任务）")
    parser.add_argument("--spark-cmd", type=str, default=None,
                        help="收到事件时执行的命令，支持 {item_key} {pdf_path} {payload_path} "
                             "{chapter1_path} {ppt_path} {topic} 占位符")
    parser.add_argument("--only", choices=["paper_ingested", "review_ready"], default=None,
                        help="只对某类事件执行 --spark-cmd")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    event = read_event()
    if not event:
        print("⚠️  [spark-hook] 未收到事件内容（stdin 与 CNKI_EVENT 均为空）")
        return

    kind = str(event.get("event", "unknown"))
    append_jsonl(Path(args.log), event)
    task_path = write_task(Path(args.tasks_dir), event)

    if kind == "paper_ingested":
        print(f"🔔 [spark-hook] 新文献入库: {event.get('item_key')} 《{event.get('title', '')}》"
              f" 队列 {event.get('queue_count')}/{event.get('batch_size')}")
    elif kind == "review_ready":
        print(f"🎉 [spark-hook] 综述已就绪（批次 #{event.get('batch_id')}，"
              f"{event.get('paper_count')} 篇）: {event.get('chapter1_path')}")
    else:
        print(f"🔔 [spark-hook] 事件 {kind}")
    print(f"   任务单: {task_path}")

    if args.spark_cmd and (args.only is None or args.only == kind):
        cmd = args.spark_cmd
        for field in ("item_key", "pdf_path", "payload_path", "chapter1_path", "ppt_path", "topic"):
            cmd = cmd.replace("{" + field + "}", str(event.get(field, "") or ""))
        cmd = cmd.replace("{task_path}", str(task_path))
        print(f"   ▶️  调用 Spark: {cmd}")
        try:
            proc = subprocess.run(cmd, shell=True, timeout=3600)
            print(f"   ✅ Spark 退出码: {proc.returncode}")
        except Exception as exc:
            print(f"   ⚠️  Spark 调用失败: {exc}")


if __name__ == "__main__":
    main()
