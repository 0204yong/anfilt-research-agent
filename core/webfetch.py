"""레퍼런스 URL의 본문 텍스트를 로컬에서 추출한다.

모든 LLM에 동일한 원문을 제공하기 위해 프로그램이 직접 페이지를 가져와
텍스트를 뽑는다 (프로바이더별 fetch 기능 차이에 영향받지 않음).
"""
import requests
from bs4 import BeautifulSoup

MAX_CHARS_PER_URL = 20_000
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}


def fetch_url_text(url: str, timeout: int = 20) -> str:
    """URL 본문 텍스트를 추출한다. 실패 시 오류 설명 문자열을 반환."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return f"[가져오기 실패: {e}]"

    content_type = resp.headers.get("content-type", "")
    if "pdf" in content_type:
        return "[PDF 문서 — 본문 자동 추출 미지원. 웹 검색 결과로 보완 필요]"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    text = " ".join(soup.get_text(separator=" ").split())
    if not text:
        return "[본문 텍스트를 찾지 못함]"
    if len(text) > MAX_CHARS_PER_URL:
        text = text[:MAX_CHARS_PER_URL] + " …(이하 생략)"
    return text


def fetch_references(urls: list) -> dict:
    """{url: 본문 텍스트} 매핑을 반환."""
    return {url: fetch_url_text(url) for url in urls if url.strip()}
