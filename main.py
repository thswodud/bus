import math
from datetime import datetime, timedelta, time
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests
import streamlit as st


# ------------------------------------------------------------
# 기본 설정
# ------------------------------------------------------------
st.set_page_config(
    page_title="버스 출발 타이밍 도우미",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded",
)

KST = ZoneInfo("Asia/Seoul")
BUS_STOP_URL = "https://apis.data.go.kr/1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList"
BUS_ARRIVAL_URL = "https://apis.data.go.kr/1613000/ArvlInfoInqireService/getSttnAcctoArvlPrearngeInfoList"

st.markdown(
    """
    <style>
    .block-container {max-width: 1120px; padding-top: 1.8rem; padding-bottom: 3rem;}
    .hero {
        padding: 1.5rem 1.6rem; border-radius: 18px;
        background: linear-gradient(120deg, #12334a 0%, #176b75 100%);
        color: white; margin-bottom: 1rem;
    }
    .hero h1 {font-size: 2rem; margin: 0 0 .45rem 0; color: white;}
    .hero p {margin: 0; opacity: .92; font-size: 1rem;}
    .small-note {font-size: .88rem; color: #687782;}
    div[data-testid="stMetric"] {background: rgba(120, 130, 140, .08); padding: .9rem; border-radius: 12px;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# 공통 함수
# ------------------------------------------------------------
def get_secret_key():
    """Streamlit Cloud Secrets에 저장된 인증키를 읽는다."""
    try:
        return str(st.secrets.get("TAGO_SERVICE_KEY", "")).strip()
    except Exception:
        return ""


def normalize_service_key(key):
    """공공데이터포털에서 복사한 URL 인코딩 키도 처리한다."""
    return unquote(str(key or "").strip())


def get_value(item, *names, default=""):
    for name in names:
        value = item.get(name)
        if value is not None and value != "":
            return value
    return default


def response_items(data):
    """TAGO JSON 응답에서 item 목록을 꺼낸다. 단일 항목/목록 응답을 모두 처리한다."""
    root = data.get("response", data)
    header = root.get("header", {}) or {}
    code = str(header.get("resultCode", "00"))
    message = str(header.get("resultMsg", "정상 응답"))

    if code not in ("00", "0", "NORMAL_CODE"):
        raise ValueError(f"API 응답 오류({code}): {message}")

    body = root.get("body", {}) or {}
    items = body.get("items", {})
    if items is None:
        return []
    if isinstance(items, dict):
        found = items.get("item", [])
    else:
        found = items

    if found is None or found == "":
        return []
    if isinstance(found, list):
        return [row for row in found if isinstance(row, dict)]
    if isinstance(found, dict):
        return [found]
    return []


def call_tago_api(url, params, service_key):
    """TAGO API를 호출하고 응답을 JSON으로 변환한다."""
