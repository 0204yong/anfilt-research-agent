"""조사 결과 → 지식볼트(Obsidian) zip 내보내기.

로드맵 1단계 (→ docs/13 지식 볼트와 온톨로지): 파이프라인 결과를
사람용 마크다운 노트 + 기계용 run.json으로 변환해 zip 바이트로 반환한다.
Streamlit 비의존 (→ docs/02 파이프라인 설계 원칙).

zip 구조 (기존 볼트 폴더 위에 풀면 runs/ 아래로 쌓인다):
    지식볼트/runs/<날짜> <주제>.md        ← 보고서·채점표·개별 조사·토론 전문
    지식볼트/runs/<날짜> <주제>.run.json  ← brief + params + result 원본
                                            (2단계 Supabase ra_runs에 그대로 적재 가능)
"""
import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import asdict

SCHEMA_VERSION = 1

# Windows 예약 장치명 — 파일명 첫 토큰이 이거면 밑줄을 붙여 회피
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_filename(name: str, max_len: int = 50) -> str:
    """한글·공백은 유지하되 Windows 금지 문자·예약명·꼬리 점을 정리한다."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(name))
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "untitled"
    if name.split(".")[0].split(" ")[0].upper() in _WIN_RESERVED:
        name = "_" + name
    return name[:max_len].rstrip(". ") or "untitled"


def _yaml_str(value) -> str:
    """frontmatter 안전 문자열 — JSON 이중따옴표 스칼라는 YAML로도 유효하다."""
    return json.dumps(str(value), ensure_ascii=False)


def _md_cell(value) -> str:
    """마크다운 표 셀 이스케이프."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def make_run_id(executed_at: str) -> str:
    """로컬 run_id. 2단계(Supabase) 도입 후에도 이 id를 그대로 키로 쓴다."""
    stamp = re.sub(r"[^0-9]", "", executed_at)[:14]
    return f"run-{stamp}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------- run.json


def build_run_record(brief, params: dict, result, run_id: str,
                     executed_at: str) -> dict:
    """기계용 원본 레코드. asdict 전체 + 레퍼런스 프로비넌스.

    프로바이더가 API 차원의 citations를 버리므로(→ docs/13 진단),
    수집 해시·에러 여부는 저장 시점에 여기서 새로 기록한다.
    """
    brief_d = asdict(brief)
    references_meta = []
    for source, text in (brief_d.get("reference_texts") or {}).items():
        text = str(text)
        references_meta.append({
            "source": source,
            "is_attachment": source.startswith("[첨부 파일]"),
            "is_error": text.startswith("["),  # '[' 접두사 = 수집 실패 관례
            "chars": len(text),
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "fetched_at": executed_at,  # 수집 시각 미기록 → 실행 시각으로 근사
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "executed_at": executed_at,
        "brief": brief_d,
        "params": params,
        "references_meta": references_meta,
        "result": asdict(result),
    }


# ------------------------------------------------------------ 마크다운 노트


def _render_report_md(report: dict) -> list:
    lines = []
    if report.get("executive_summary"):
        lines += ["### Executive Summary", "", report["executive_summary"], ""]
    if report.get("key_findings"):
        lines += ["### 핵심 발견사항", ""]
        lines += [f"{i}. {item}" for i, item in enumerate(report["key_findings"], 1)]
        lines.append("")
    for sec in report.get("sections", []):
        lines += [f"### {sec.get('heading', '')}", "", sec.get("content", ""), ""]
        bullets = sec.get("bullets") or []
        if bullets:
            lines += [f"- {b}" for b in bullets]
            lines.append("")
    for t in report.get("data_tables", []):
        headers, rows = t.get("headers", []), t.get("rows", [])
        if not (headers and rows):
            continue
        lines += [f"### 📊 {t.get('title', '')}", ""]
        lines.append("| " + " | ".join(_md_cell(h) for h in headers) + " |")
        lines.append("|" + "---|" * len(headers))
        for r in rows:
            padded = list(r) + [""] * (len(headers) - len(r))
            lines.append("| " + " | ".join(_md_cell(c) for c in padded) + " |")
        lines.append("")
    if report.get("recommendations"):
        lines += ["### 제언", ""]
        lines += [f"{i}. {item}"
                  for i, item in enumerate(report["recommendations"], 1)]
        lines.append("")
    if report.get("sources"):
        lines += ["### 출처", ""]
        lines += [f"- [{s.get('title', '')}]({s.get('url', '')})"
                  for s in report["sources"]]
        lines.append("")
    return lines


def _render_scorecard_md(scorecard: dict, anon_map: dict) -> list:
    if not scorecard or not scorecard.get("evaluations"):
        return []

    def disp(name):
        real = (anon_map or {}).get(name)
        return f"{name} ({real})" if real else name

    lines = ["## 채점표", ""]
    if scorecard.get("best"):
        lines += [f"**🏅 베스트: {disp(scorecard['best'])}** — "
                  f"{scorecard.get('rationale', '')}", ""]
    evs = scorecard["evaluations"]
    crit_order = []
    for ev in evs:
        for s in ev.get("scores", []):
            if s["criterion"] not in crit_order:
                crit_order.append(s["criterion"])
    names = [disp(ev.get("researcher", "?")) for ev in evs]
    lines.append("| 채점 기준 | " + " | ".join(_md_cell(n) for n in names) + " |")
    lines.append("|---|" + "---|" * len(names))
    for crit in crit_order:
        row = [_md_cell(crit)]
        for ev in evs:
            score = next((s["score"] for s in ev.get("scores", [])
                          if s["criterion"] == crit), "")
            row.append(str(score))
        lines.append("| " + " | ".join(row) + " |")
    totals = [str(ev.get("total", "")) for ev in evs]
    lines.append("| **합계** | " + " | ".join(totals) + " |")
    lines.append("")
    for ev in evs:
        lines.append(f"**{disp(ev.get('researcher', '?'))}** — "
                     f"강점: {ev.get('strengths', '')} / "
                     f"약점: {ev.get('weaknesses', '')}")
    lines.append("")
    return lines


def render_run_note(brief, params: dict, result, run_id: str,
                    executed_at: str) -> str:
    """사람용 Obsidian 노트. 환각 세탁 방지를 위해 미검증 라벨을 강제한다
    (→ docs/13 설계 원칙 1: 전제가 아닌 대조 대상)."""
    report = result.report or {}
    as_of = executed_at[:10]
    provider_labels = [f.provider_label for f in result.findings]

    lines = [
        "---",
        "type: run",
        f"run_id: {run_id}",
        f"executed_at: {executed_at}",
        f"as_of: {as_of}",
        f"topic: {_yaml_str(brief.topic)}",
        "providers: [" + ", ".join(_yaml_str(p) for p in provider_labels) + "]",
        f"moderator: {_yaml_str(result.moderator_label)}",
        f"rounds: {params.get('rounds', '')}",
        f"mode: {params.get('mode', '')}",
        f"target_pages: {params.get('target_pages', '')}",
        "verified: false",
        "tags: [research-run]",
        "---",
        "",
        "> [!warning] 과거 조사 결과 (자동 생성·미검증)",
        f"> 멀티 LLM 조사의 자동 저장본입니다 (as_of {as_of}). "
        "검증된 사실이 아니라 **대조 대상**으로 사용하세요.",
        "",
        f"# {report.get('title') or brief.topic}",
        "",
    ]
    if report.get("subtitle"):
        lines += [f"*{report['subtitle']}*", ""]

    # ---- 조사 개요
    lines += ["## 조사 개요", "", f"- **주제**: {brief.topic}"]
    if brief.keywords:
        lines.append("- **키워드**: " + ", ".join(brief.keywords))
    if brief.instructions:
        lines.append(f"- **지시사항**: {brief.instructions}")
    lines.append(f"- **참여 LLM**: {', '.join(provider_labels)} · "
                 f"토론 {params.get('rounds', '?')}라운드 · "
                 f"진행자 {result.moderator_label}")
    if brief.reference_texts:
        lines.append("- **레퍼런스**:")
        for source, text in brief.reference_texts.items():
            ok = not str(text).startswith("[")
            lines.append(f"    - {'✅' if ok else '⚠️'} {source}")
    lines.append("")

    # ---- 최종 보고서 / 채점표
    lines += ["## 최종 보고서", ""]
    lines += _render_report_md(report)
    lines += _render_scorecard_md(result.scorecard, result.anon_map)

    # ---- 개별 조사 결과
    lines += ["## 개별 조사 결과", ""]
    for f in result.findings:
        lines += [f"### {f.provider_label} ({f.model})", ""]
        lines += [f"⚠️ 조사 실패: {f.error}" if f.error else f.text, ""]

    # ---- 토론
    if result.discussion:
        lines += ["## 토론", ""]
        current_round = None
        for t in result.discussion:
            if t.round_no != current_round:
                current_round = t.round_no
                lines += [f"### {current_round}차 토론", ""]
            lines += [f"#### {t.provider_label}", ""]
            lines += [f"⚠️ 발언 실패: {t.error}" if t.error else t.text, ""]

    return "\n".join(lines)


# ------------------------------------------------------------------ zip


def build_vault_zip(brief, params: dict, result, executed_at: str,
                    run_id: str = None) -> tuple:
    """(zip 파일명, zip bytes) 반환. 노트와 run.json을 볼트 구조로 묶는다.

    run_id를 주면 그대로 쓴다 — Supabase 아카이브(ra_runs)와 zip이
    같은 id를 공유해 상호 참조가 가능해진다 (2단계 연동).
    """
    run_id = run_id or make_run_id(executed_at)
    day = executed_at[:10]
    stem = f"{day} {_safe_filename(brief.topic)}"
    note = render_run_note(brief, params, result, run_id, executed_at)
    record = build_run_record(brief, params, result, run_id, executed_at)

    buf = io.BytesIO()
    # zipfile은 비ASCII 이름에 UTF-8 플래그를 자동 설정 → 윈도 탐색기에서 한글 안전
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"지식볼트/runs/{stem}.md", note)
        zf.writestr(
            f"지식볼트/runs/{stem}.run.json",
            json.dumps(record, ensure_ascii=False, indent=2),
        )
    fname = f"지식볼트_{day}_{_safe_filename(brief.topic, 30)}.zip"
    return fname, buf.getvalue()
