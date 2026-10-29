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
    """TAGO JSON 응답에서 item 목록을 꺼내고 API 오류를 구분한다."""
    if not isinstance(data, dict):
        raise RuntimeError("API 응답 형식이 예상과 달라요. 공공데이터포털의 응답 형식을 확인해 주세요.")

    # 공공데이터포털 게이트웨이가 반환하는 인증/서비스 오류 형식
    gateway_error = data.get("OpenAPI_ServiceResponse")
    if gateway_error:
        header = gateway_error.get("cmmMsgHeader", {}) if isinstance(gateway_error, dict) else {}
        message = get_value(header, "errMsg", "returnAuthMsg", "returnReasonCode", default="API 게이트웨이 오류")
        raise RuntimeError(f"공공데이터포털 API 오류: {message}. API 신청 상태와 인증키를 확인해 주세요.")

    root = data.get("response", data)
    if not isinstance(root, dict):
        raise RuntimeError("API 응답 형식이 올바르지 않아요.")

    header = root.get("header", {}) or {}
    code = str(header.get("resultCode", "00"))
    message = str(header.get("resultMsg", "정상 응답"))

    if code not in ("00", "0", "0000", "NORMAL_CODE"):
        if code in ("20", "30") or "SERVICE_KEY" in message.upper() or "인증" in message:
            raise RuntimeError("인증키 오류 또는 API 이용 권한 문제예요. 공공데이터포털에서 버스도착정보 API 활용신청과 인증키를 확인해 주세요.")
        raise RuntimeError(f"API 응답 오류({code}): {message}")

    body = root.get("body", {}) or {}
    if not isinstance(body, dict):
        return []
    items = body.get("items", {})
    if items is None:
